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

logger = logging.getLogger(__name__)

# Per-section budget. Sections are small, focused components — even a complex
# pricing table fits in <8K tokens of JSX. 16K leaves room for thorough copy.
_SECTION_MAX_TOKENS = 16000


# ── Hero archetype anatomy library ───────────────────────────────────
# Each entry is a STRUCTURAL skeleton — what goes where, sized in what scale.
# Claude has freedom over class choices and copy positioning *within* the
# archetype, but the archetype itself dictates layout intent. The Brief picks
# one per project, so output structurally varies across projects.
#
# Why a library, not a single prescription: the round-2 result was visually
# repetitive because every hero used the same "full-bleed darken + center copy"
# rule. Real-world top sites use 6-10 distinct hero patterns; we pick from
# them rather than hard-coding one.
_HERO_ARCHETYPES: dict[str, str] = {
    "full-bleed-overlay": (
        "ARCHETYPE: full-bleed-overlay (cinematic, editorial — used by El Toro, Tonight Is a Good Night for Tapas).\n"
        "  ANATOMY:\n"
        "    • <section> is `relative isolate min-h-[600px] md:min-h-[720px] lg:min-h-[820px] overflow-hidden`.\n"
        "    • Layer 1 (z-0): <Image fill priority> from section.images[0] as full-bleed background, `object-cover`.\n"
        "    • Layer 2 (z-10): absolute-inset gradient overlay — `bg-gradient-to-t from-foreground/85 via-foreground/40 to-foreground/20`\n"
        "      OR `bg-gradient-to-r from-foreground/85 via-foreground/40 to-transparent` if you place copy bottom-LEFT.\n"
        "    • Layer 3 (z-20): foreground container `relative max-w-3xl px-6 sm:px-10 lg:px-16 py-24 md:py-32`,\n"
        "      placed bottom-left or middle-left (NEVER centered):\n"
        "        - Eyebrow: small caps, tracking-widest, text-primary or text-accent (text-xs md:text-sm)\n"
        "        - Headline: display, text-5xl md:text-6xl lg:text-7xl, leading-[1.05], tracking-tight,\n"
        "          text-background (white). Optionally render one accent word in italic OR text-primary.\n"
        "        - Subheadline: text-lg md:text-xl text-background/80 max-w-xl\n"
        "        - CTA row: primary CTA pill + ghost-outline secondary, gap-4, mt-8\n"
        "    • Optional bottom-center scroll cue (animated chevron-down or thin pulsing line, text-background/60).\n"
        "  WRAP all foreground content in `<Reveal variant=\"fade-up\">` with staggered delays."
    ),
    "oversized-watermark": (
        "ARCHETYPE: oversized-watermark (massive bg type behind product — HOCN \"VICTORIA\", Setto \"Precision Delivery\", Fujifilm).\n"
        "  ANATOMY:\n"
        "    • <section> is `relative isolate bg-background min-h-[640px] md:min-h-[760px] overflow-hidden`.\n"
        "    • Layer 1 (z-0): an oversized watermark word — pick ONE strong word from the headline (or use brand name).\n"
        "      Render it as a single span absolutely positioned center-ish, with `text-[clamp(8rem,18vw,18rem)] font-black\n"
        "      tracking-[-0.04em] leading-none text-foreground/[0.06] select-none whitespace-nowrap` and rotate or shift\n"
        "      so it bleeds off one edge. This is the visual anchor.\n"
        "    • Layer 2 (z-10): two-column grid `grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-16 items-center px-6 lg:px-12 py-20 lg:py-28`.\n"
        "        - Copy column (`lg:col-span-6`): Eyebrow → headline (text-5xl md:text-6xl lg:text-7xl, font-bold) →\n"
        "          subheadline → CTA pair. Pin to top or middle.\n"
        "        - Image column (`lg:col-span-6`): hero <Image fill> in a tall `aspect-[4/5] lg:aspect-[3/4]` frame,\n"
        "          rounded-3xl, shadow-2xl, possibly slightly tilted with `rotate-1` or `lg:translate-x-4`.\n"
        "    • Optional floating numeric badge bottom-right of the image (`absolute -bottom-4 -left-4 bg-card border\n"
        "      border-border rounded-2xl shadow-lg p-4`) — rating, user count, or stat (read from items if present)."
    ),
    "asymmetric-split": (
        "ARCHETYPE: asymmetric-split (uneven 2-col, copy + product — Chanel diffuser, Veloretti Electric Ace).\n"
        "  ANATOMY:\n"
        "    • <section> is `bg-background min-h-[600px] md:min-h-[760px] overflow-hidden`.\n"
        "    • Top-level: `grid grid-cols-1 lg:grid-cols-12 gap-y-12 lg:gap-x-12 items-center px-6 lg:px-12 py-20 lg:py-28`.\n"
        "    • Copy col (`lg:col-span-7` or `lg:col-span-6` — pick UNEVEN): centered vertically.\n"
        "        - Small underline/dash element above eyebrow (`h-px w-12 bg-primary mb-6`).\n"
        "        - Eyebrow → display headline (text-5xl md:text-6xl lg:text-7xl, font-bold or font-semibold,\n"
        "          tracking-tight, leading-[1.05]). Break across 2-3 lines; render 1 accent word in italic\n"
        "          serif OR `text-primary`.\n"
        "        - Subheadline (text-base md:text-lg text-muted-foreground max-w-md).\n"
        "        - CTA pair, gap-4.\n"
        "    • Image col (`lg:col-span-5` or `lg:col-span-6` — the OTHER size): hero <Image fill> inside a tall\n"
        "      aspect-[4/5] container, rounded-3xl, shadow-xl. Add ONE decorative absolute element breaking the edge\n"
        "      (a thin ring, dot grid, soft blob in `bg-primary/10`, or a small floating accent card).\n"
        "    • Bottom edge (full-width, optional): a thin row of 4-5 stat / trust signals if section.items exists\n"
        "      (`flex items-center gap-8 pt-12 border-t border-border text-sm text-muted-foreground`)."
    ),
    "type-wrapping-product": (
        "ARCHETYPE: type-wrapping-product (giant headline split around centered product — Setto Precision Delivery).\n"
        "  ANATOMY:\n"
        "    • <section> is `relative isolate bg-background min-h-[700px] md:min-h-[820px] overflow-hidden`.\n"
        "    • Background headline split: take the headline's two strongest words (e.g. 'Precision' / 'Delivery').\n"
        "        - Word 1: absolute top-6 left-6 (or top-12 left-12 on lg), text-[clamp(4rem,12vw,11rem)] font-black\n"
        "          tracking-[-0.04em] leading-none text-foreground.\n"
        "        - Word 2: absolute bottom-6 right-6 (or bottom-12 right-12), same scale, same weight.\n"
        "        - These words are huge — they ARE the visual frame.\n"
        "    • Center: hero <Image fill> in a `relative z-10 mx-auto max-w-2xl aspect-square` (or aspect-[4/5]) frame,\n"
        "      possibly with a subtle rotation (`rotate-2` or `-rotate-3`).\n"
        "    • Right edge (z-20, hidden on small screens): 2-3 floating mini-cards stacked vertically with `absolute\n"
        "      right-6 top-1/3 space-y-3 hidden lg:flex flex-col`. Each card is small (`w-56`), `bg-card border border-border\n"
        "      rounded-2xl shadow-md p-4`, showing a stat / testimonial / trust badge from section.items.\n"
        "    • Bottom-left absolute: primary CTA pill + 1-line subheadline. Eyebrow may sit above-left."
    ),
    "video-mask": (
        "ARCHETYPE: video-mask (large hero photo with circular play overlay + side card stack — Architecture, Coffee Cups).\n"
        "  ANATOMY:\n"
        "    • <section> is `bg-background min-h-[640px] md:min-h-[760px]`.\n"
        "    • Top-level: `grid grid-cols-1 lg:grid-cols-12 gap-6 lg:gap-10 items-stretch px-6 lg:px-12 py-16 lg:py-24`.\n"
        "    • Left col (`lg:col-span-7`): hero <Image fill> in a relative aspect-[4/3] lg:aspect-[3/2] container,\n"
        "      rounded-3xl, overflow-hidden. Layer a CIRCULAR play button absolute centered:\n"
        "        `absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 h-20 w-20 md:h-24 md:w-24 rounded-full\n"
        "        bg-foreground text-background flex items-center justify-center shadow-2xl transition hover:scale-110`\n"
        "      with a `<Play className=\"h-8 w-8 ml-1\" />` icon. Aria-label 'Play intro'.\n"
        "    • Right col (`lg:col-span-5`): vertical flex.\n"
        "        - Top: eyebrow → headline (text-4xl md:text-5xl lg:text-6xl, can include italic accent word) →\n"
        "          subheadline.\n"
        "        - Middle: two stacked smaller image-cards from section.images[1..2] OR cards built from\n"
        "          section.items, each `aspect-[4/3] rounded-2xl shadow-md hover:scale-[1.02] transition`.\n"
        "        - Bottom: a thin row with index counter (e.g. '03') + prev/next arrow controls + primary CTA pill."
    ),
    "card-stack": (
        "ARCHETYPE: card-stack (copy + multiple overlapping rotated cards on side — Architecture, Coffee Cups).\n"
        "  ANATOMY:\n"
        "    • <section> is `bg-background min-h-[640px] md:min-h-[760px] overflow-hidden`.\n"
        "    • Top-level: `grid grid-cols-1 lg:grid-cols-12 gap-12 items-center px-6 lg:px-12 py-20 lg:py-28`.\n"
        "    • Copy col (`lg:col-span-6`): eyebrow → headline (text-5xl md:text-6xl lg:text-7xl, font-bold) →\n"
        "      subheadline → CTA pair. Optional: a row of 3 small trust logos OR a stat triple along bottom.\n"
        "    • Cards col (`lg:col-span-6`): a relative container, `min-h-[480px]`, that hosts 3 absolute cards:\n"
        "        - Card A (largest): `top-0 left-0 w-[80%] aspect-[3/4] rotate-[-4deg] z-30` — main hero <Image>.\n"
        "        - Card B (medium): `top-12 right-0 w-[58%] aspect-square rotate-[6deg] z-20` — second image OR\n"
        "          a stat card with bg-card border-border + a big number from section.items[0].value.\n"
        "        - Card C (small): `bottom-0 left-12 w-[44%] aspect-[4/5] rotate-[-2deg] z-10` — third image OR\n"
        "          a quote / testimonial card.\n"
        "      Each card: `rounded-3xl overflow-hidden shadow-2xl border border-border`."
    ),
}


# ── Menu archetype anatomy ────────────────────────────────────────────
_MENU_ARCHETYPES: dict[str, str] = {
    "two-column-dotted": (
        "ARCHETYPE: two-column-dotted (editorial menu — text rows w/ dotted leaders, El Toro-style).\n"
        "  ANATOMY:\n"
        "    • Group section.items by `item.label` (category). Render each category as its own block.\n"
        "    • Category header: small uppercase eyebrow with a 1-px primary underline (`text-xs tracking-[0.25em]\n"
        "      text-primary mb-6 inline-flex items-center gap-3 before:content-[''] before:h-px before:w-8\n"
        "      before:bg-primary`).\n"
        "    • Items in `grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-6` per category.\n"
        "    • Row: top line is `flex items-baseline gap-3` — dish name (font-semibold text-lg) on LEFT,\n"
        "      a flex-1 dotted leader (`flex-1 border-b border-dotted border-foreground/20 mx-2`),\n"
        "      price (item.value, font-semibold text-primary) on RIGHT.\n"
        "      Below: description (text-sm text-muted-foreground, max-w-md).\n"
        "    • DO NOT render images per row. No item cards / borders. Pure typography."
    ),
    "photo-card-grid": (
        "ARCHETYPE: photo-card-grid (photo-led product cards — Coffee TV / Coffee Cups menus).\n"
        "  ANATOMY:\n"
        "    • Group items by category. For EACH category: small category header, then a 3 or 4-col grid\n"
        "      `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 md:gap-6`.\n"
        "    • Each item card: relative aspect-[4/5] OR aspect-square, rounded-2xl overflow-hidden bg-card.\n"
        "      If item has image_query (rendered as section.images[i].url at runtime), use <Image fill> as bg.\n"
        "      If no image, use a soft gradient placeholder (`bg-gradient-to-br from-muted to-muted/40`)\n"
        "      with a centered Lucide icon (Coffee/UtensilsCrossed/Wine/Cake based on item.label).\n"
        "    • Bottom strip overlay: `absolute bottom-0 inset-x-0 p-4 bg-gradient-to-t from-foreground/80\n"
        "      to-transparent`. Inside: dish name (font-semibold text-background text-lg), small price\n"
        "      pill (`inline-flex bg-background/90 text-foreground rounded-full px-3 py-1 text-sm font-medium`).\n"
        "    • Hover: scale image 105%, lift card."
    ),
    "categorized-rows": (
        "ARCHETYPE: categorized-rows (single-column photo+text rows grouped by category — Drink TV menu).\n"
        "  ANATOMY:\n"
        "    • Single column, vertical stack of category blocks. Each category:\n"
        "      Category header (h3, font-bold text-2xl uppercase tracking-wide text-primary, with a h-px\n"
        "      bg-border alongside).\n"
        "      Then items in a stacked list (no grid).\n"
        "    • Each item row: `flex items-center gap-6 py-4 border-b border-border/50 last:border-0`.\n"
        "      Left: 64x64 round photo (rounded-full bg-muted overflow-hidden) using image_query if present,\n"
        "      or a Lucide icon centered if not.\n"
        "      Middle (flex-1): dish name (font-semibold text-base) + description (text-sm text-muted-foreground).\n"
        "      Right: price (item.value, font-semibold text-primary text-lg).\n"
        "    • Hover row: bg-muted/40 transition. Cursor-default.\n"
        "    • Optional: pin a small CTA at the bottom (e.g. 'View full menu PDF' link)."
    ),
}


# ── Gallery archetype anatomy ────────────────────────────────────────
_GALLERY_ARCHETYPES: dict[str, str] = {
    "asymmetric-12col": (
        "ARCHETYPE: asymmetric-12col (curated 12-col grid with varying tile spans).\n"
        "  ANATOMY:\n"
        "    • Container: `grid grid-cols-2 md:grid-cols-12 gap-3 md:gap-4 auto-rows-[140px] md:auto-rows-[180px]`.\n"
        "    • Tile spans (cycle through these for each image):\n"
        "        i=0: `md:col-span-7 md:row-span-2` (large feature)\n"
        "        i=1: `md:col-span-5`\n"
        "        i=2: `md:col-span-5 md:row-span-2`\n"
        "        i=3: `md:col-span-4`\n"
        "        i=4: `md:col-span-3`\n"
        "        i=5: `md:col-span-8`\n"
        "        (additional images repeat the cycle).\n"
        "    • Each tile: `relative overflow-hidden rounded-2xl group`. <Image fill> with `object-cover\n"
        "      transition-transform duration-700 group-hover:scale-105`. Optional caption overlay on hover\n"
        "      (`absolute inset-x-0 bottom-0 p-3 bg-gradient-to-t from-foreground/80 to-transparent\n"
        "      opacity-0 group-hover:opacity-100 transition`)."
    ),
    "marquee-scroll": (
        "ARCHETYPE: marquee-scroll (horizontal infinite scroll of photo cards).\n"
        "  ANATOMY:\n"
        "    • Single horizontal row that overflows: `relative w-full overflow-hidden`.\n"
        "    • Inner track: `flex gap-6 animate-[marquee_40s_linear_infinite]` (define keyframes inline\n"
        "      via Tailwind arbitrary properties OR add the keyframes via `<style jsx global>`).\n"
        "    • Each card: `relative w-[280px] md:w-[360px] aspect-[4/5] flex-none rounded-3xl overflow-hidden`.\n"
        "      <Image fill object-cover>. Hover pauses (`hover:[animation-play-state:paused]` on track).\n"
        "    • Duplicate the images list once inside the track so the marquee loops seamlessly.\n"
        "    • Optional fade gradients on left/right edges (`absolute inset-y-0 w-24 from-background\n"
        "      to-transparent` left/right pointing).\n"
        "    • Above the marquee: heading block (eyebrow, h2, subhead) — left-aligned, max-w-2xl."
    ),
    "bento-mosaic": (
        "ARCHETYPE: bento-mosaic (modern bento grid with strong mixed sizes).\n"
        "  ANATOMY:\n"
        "    • Container: `grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4`.\n"
        "    • Tile spans (cycle):\n"
        "        i=0: `md:col-span-2 md:row-span-2 aspect-square` (hero tile — large square)\n"
        "        i=1: `aspect-[4/5]`\n"
        "        i=2: `aspect-square`\n"
        "        i=3: `md:col-span-2 aspect-[2/1]` (wide)\n"
        "        i=4: `aspect-[4/5]`\n"
        "        i=5: `aspect-square`\n"
        "    • Each tile: `relative overflow-hidden rounded-3xl group`, <Image fill object-cover>,\n"
        "      hover: scale-[1.04] + slight contrast lift.\n"
        "    • DO NOT add captions unless item.label exists — pure imagery."
    ),
}


# ── Testimonials archetype anatomy ────────────────────────────────────
_TESTIMONIALS_ARCHETYPES: dict[str, str] = {
    "glass-cards-bg": (
        "ARCHETYPE: glass-cards-bg (translucent cards floating over a textured photo bg — El Toro).\n"
        "  ANATOMY:\n"
        "    • <section> is `relative isolate min-h-[600px] py-20 lg:py-28 overflow-hidden`.\n"
        "    • Layer 1 (z-0): if section.images[0] present, render as <Image fill object-cover> bg;\n"
        "      otherwise use `bg-gradient-to-br from-muted to-card`.\n"
        "    • Layer 2 (z-10): `absolute inset-0 bg-foreground/70` for darken (only if bg image used).\n"
        "    • Layer 3 (z-20): heading block top-left (eyebrow + h2 in display serif italic if available\n"
        "      + subhead), text-background.\n"
        "    • Cards grid: `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 mt-12`.\n"
        "    • Each card: `bg-card/10 backdrop-blur-md border border-background/15 rounded-2xl p-6\n"
        "      text-background`. Star row at top (5 stars from lucide-react Star, fill-primary text-primary).\n"
        "      Italic body text. Bottom: avatar circle (initials, bg-primary/20 text-background) + name +\n"
        "      role/location (text-background/70).\n"
        "    • Wrap each card in <Reveal variant=\"fade-up\" delay={{i*80}}>."
    ),
    "marquee-row": (
        "ARCHETYPE: marquee-row (horizontal scrolling testimonial pills).\n"
        "  ANATOMY:\n"
        "    • Heading block above: eyebrow + h2 + subhead, centered or left-aligned.\n"
        "    • Marquee track: `relative w-full overflow-hidden`. Inside: `flex gap-4 animate-[marquee_45s_linear_infinite]`\n"
        "      with the items duplicated.\n"
        "    • Each item card: `flex-none w-[340px] md:w-[420px] bg-card border border-border rounded-2xl\n"
        "      p-6 shadow-sm`. Top: small star row (fill-primary). Body: text-sm leading-relaxed.\n"
        "      Bottom: `flex items-center gap-3` — avatar (rounded-full bg-primary/10 text-primary text-sm\n"
        "      font-semibold initials) + name (font-semibold) + role (text-xs text-muted-foreground).\n"
        "    • Hover pauses marquee.\n"
        "    • Run TWO opposite-direction rows for visual interest (one scrolls left, one scrolls right)\n"
        "      if there are 6+ items, otherwise just one row."
    ),
    "big-quote-portrait": (
        "ARCHETYPE: big-quote-portrait (one huge quote w/ author portrait, smaller cards underneath).\n"
        "  ANATOMY:\n"
        "    • Two-row layout. Row 1: `grid grid-cols-1 lg:grid-cols-12 gap-10 items-center`.\n"
        "        - Left col (`lg:col-span-5`): if section.images[0] exists, render a tall portrait\n"
        "          (`relative aspect-[4/5] rounded-3xl overflow-hidden`). Otherwise show large initials\n"
        "          avatar (`flex h-72 w-72 items-center justify-center rounded-full bg-primary/10\n"
        "          text-primary text-7xl font-bold`).\n"
        "        - Right col (`lg:col-span-7`): giant Quote icon (h-12 w-12 text-primary), then the\n"
        "          PRIMARY quote in display serif (`text-3xl md:text-4xl lg:text-5xl font-medium\n"
        "          leading-tight tracking-tight`). Below: 5-star row + author name (font-semibold) +\n"
        "          role + small `verified` badge if present.\n"
        "    • Row 2 (smaller cards): `grid grid-cols-1 md:grid-cols-3 gap-4 mt-12`. Render 2-3 SECONDARY\n"
        "      testimonials as compact cards: `bg-card border border-border rounded-xl p-5`. Star row,\n"
        "      one-line quote (line-clamp-2), name + role at bottom."
    ),
}


# ── Features archetype anatomy ────────────────────────────────────────
_FEATURES_ARCHETYPES: dict[str, str] = {
    "icon-grid-3": (
        "ARCHETYPE: icon-grid-3 (3-col icon cards — clean SaaS pattern).\n"
        "  ANATOMY:\n"
        "    • Heading block above: eyebrow + h2 + subhead, max-w-2xl, centered or left.\n"
        "    • Grid: `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mt-12`.\n"
        "    • Each card: `bg-card border border-border rounded-2xl p-6 transition-all duration-300\n"
        "      hover:-translate-y-1 hover:shadow-lg hover:border-primary/40`.\n"
        "      Top: icon container `inline-flex h-12 w-12 items-center justify-center rounded-xl\n"
        "      bg-primary/10 text-primary mb-4` with a Lucide icon from item.icon.\n"
        "      Then h3 (font-semibold text-lg mb-2) + description (text-muted-foreground text-sm leading-relaxed).\n"
        "    • Wrap each in <Reveal variant=\"fade-up\" delay={{i*80}}>."
    ),
    "numbered-stepper": (
        "ARCHETYPE: numbered-stepper (process / how_it_works horizontal stepper).\n"
        "  ANATOMY:\n"
        "    • Heading block above. Then a horizontal stepper on lg+, vertical on mobile.\n"
        "    • Container: `relative grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-8 mt-16`.\n"
        "    • Connecting line on lg: `absolute top-6 left-12 right-12 h-px bg-border hidden lg:block` — sits\n"
        "      behind the numbered circles.\n"
        "    • Each step (z-10 to sit above the line): `relative flex flex-col items-start text-left`.\n"
        "        Numbered circle: `flex h-12 w-12 items-center justify-center rounded-full bg-primary\n"
        "        text-primary-foreground text-lg font-bold mb-4 shadow-md`. Use the index+1 (`{i+1}`).\n"
        "        h3 (font-semibold text-lg). Description (text-sm text-muted-foreground)."
    ),
    "split-image-bullets": (
        "ARCHETYPE: split-image-bullets (image left, feature list right — product-led).\n"
        "  ANATOMY:\n"
        "    • Two-col: `grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-20 items-center`.\n"
        "    • Left col: large hero photo (or section.images[0]) in `relative aspect-[4/5] rounded-3xl\n"
        "      overflow-hidden shadow-xl`. Optional decorative ring/blob breaking the edge.\n"
        "      If no image, use the dominant feature item as a large stat block (a giant primary number\n"
        "      with label).\n"
        "    • Right col: heading block (eyebrow + h2 + subhead), then a vertical list of feature rows.\n"
        "      Each row: `flex items-start gap-4 py-5 border-t border-border first:border-0`.\n"
        "        Icon column: `flex-none flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10\n"
        "        text-primary` with Lucide icon from item.icon.\n"
        "        Text column: h3 (font-semibold) + description (text-sm text-muted-foreground)."
    ),
}


# ── Header archetype anatomy ──────────────────────────────────────────
_HEADER_ARCHETYPES: dict[str, str] = {
    "transparent-pill": (
        "ARCHETYPE: transparent-pill (floating glass pill that solidifies on scroll — premium / lifestyle / restaurants).\n"
        "  ANATOMY:\n"
        "    • Wrapper `<header>` is `fixed top-0 left-0 right-0 z-50 transition-all duration-300`.\n"
        "    • Inner pill: `mx-auto mt-4 flex h-14 max-w-6xl items-center justify-between rounded-full px-6\n"
        "      transition-all duration-300`. When `scrolled` (state) → add `bg-background/80 backdrop-blur-md\n"
        "      border border-border shadow-lg`. Otherwise transparent.\n"
        "    • Left: brand name (font-semibold tracking-tight text-lg) — Link to `/`.\n"
        "    • Center (md+): nav links from `landing.nav` (text-sm font-medium text-foreground/80 hover:text-primary\n"
        "      transition-colors), gap-7. Active link gets `text-primary`.\n"
        "    • Right: primary CTA from `landing.ctas?.primary` as a small filled pill\n"
        "      (`inline-flex h-9 rounded-full bg-primary px-5 text-sm font-medium text-primary-foreground\n"
        "      hover:opacity-90 transition`). Hidden on mobile.\n"
        "    • Mobile: hamburger button (h-9 w-9 grid place-items-center) toggles a slide-down sheet\n"
        "      (`absolute inset-x-4 top-20 rounded-2xl bg-background border border-border shadow-xl p-6 space-y-3`).\n"
        "    • REQUIRED useEffect for scroll listener (set scrolled=true past 8px).\n"
    ),
    "solid-bar": (
        "ARCHETYPE: solid-bar (classic full-bleed top bar — SaaS, B2B, agencies).\n"
        "  ANATOMY:\n"
        "    • Wrapper `<header>` is `sticky top-0 z-50 w-full border-b border-border bg-background/95\n"
        "      backdrop-blur supports-[backdrop-filter]:bg-background/80`.\n"
        "    • Inner: `container mx-auto flex h-16 items-center justify-between px-4 sm:px-6 lg:px-8`.\n"
        "    • Brand left, nav center (gap-8 text-sm), CTA right.\n"
        "    • CTA right is a primary button (rounded-md, not pill).\n"
        "    • Mobile drawer below the bar: `border-t border-border bg-background` when open.\n"
    ),
    "centered-logo": (
        "ARCHETYPE: centered-logo (editorial / luxury — Chanel, Hermès style).\n"
        "  ANATOMY:\n"
        "    • Wrapper `<header>` is `sticky top-0 z-50 bg-background border-b border-border`.\n"
        "    • Inner is a 3-row grid OR a flex with brand absolutely centered:\n"
        "      `relative flex h-20 items-center justify-between px-6 lg:px-12`.\n"
        "    • Left nav: half of the nav links (text-xs uppercase tracking-[0.2em]).\n"
        "    • CENTER (absolute left-1/2 -translate-x-1/2): brand name in display serif if heading_font is serif,\n"
        "      large (text-2xl md:text-3xl font-bold tracking-tight).\n"
        "    • Right nav: the OTHER half of nav links + small CTA pill OR icon-only icons (Search, ShoppingBag).\n"
        "    • Use this when the motif is editorial / luxury / fashion / restaurant-fine-dining.\n"
    ),
    "side-rail": (
        "ARCHETYPE: side-rail (vertical fixed sidebar — portfolio / studio / agency, magazine).\n"
        "  ANATOMY:\n"
        "    • Hidden on mobile (use a top bar fallback). On lg+:\n"
        "      `<aside className=\"hidden lg:flex fixed left-0 top-0 bottom-0 w-20 z-40 bg-background\n"
        "      border-r border-border flex-col items-center py-6 gap-8\">`.\n"
        "    • Top: brand monogram (initials in a square, w-10 h-10 rounded-xl bg-primary text-primary-foreground\n"
        "      font-bold text-lg grid place-items-center).\n"
        "    • Middle: rotated nav links (`-rotate-90 origin-center` text-[11px] tracking-[0.3em] uppercase),\n"
        "      stacked vertically.\n"
        "    • Bottom: vertical social icons (Instagram, etc.) using lucide-react.\n"
        "    • Mobile (<lg): render a compact `solid-bar` style top bar instead.\n"
    ),
    "mega-menu": (
        "ARCHETYPE: mega-menu (dropdown panel showing site map — large platforms, e-com, multi-product).\n"
        "  ANATOMY:\n"
        "    • Wrapper: same pattern as solid-bar but `<nav>` items hover-open a wide dropdown panel\n"
        "      (absolute, full-width, bg-background border-y border-border shadow-2xl).\n"
        "    • Each dropdown panel: 3 columns (`grid grid-cols-3 gap-12 p-10`), each with a small uppercase eyebrow,\n"
        "      4-6 link rows, plus an optional accent card on the right.\n"
        "    • Use `useState` to track open panel.\n"
        "    • Mobile: drawer with collapsible accordion sections per top-level item.\n"
    ),
}


_FOOTER_ARCHETYPES: dict[str, str] = {
    "mega-columns": (
        "ARCHETYPE: mega-columns (4-5 column footer with brand + many link groups — SaaS, marketplaces).\n"
        "  ANATOMY:\n"
        "    • `<footer>` `bg-card border-t border-border`.\n"
        "    • Inner: `container mx-auto px-6 py-16 grid gap-10 md:grid-cols-12`.\n"
        "    • Col A (md:col-span-4): brand name large (font-bold text-xl), tagline, social icons row\n"
        "      using lucide-react (Instagram, Twitter, etc. — read from landing.brand.social).\n"
        "    • Cols B,C,D (md:col-span-2 each): titled link groups built from `landing.footer.links` split\n"
        "      into 3 buckets, each with an uppercase eyebrow + a stacked column of links (text-sm hover:text-primary).\n"
        "    • Col E (md:col-span-2): newsletter signup OR contact info from landing.brand.business_info.\n"
        "    • Bottom strip (separate row, border-t pt-6): copyright left, secondary tagline right.\n"
    ),
    "minimalist-row": (
        "ARCHETYPE: minimalist-row (single-row tight footer — fashion, lifestyle, agencies).\n"
        "  ANATOMY:\n"
        "    • `<footer>` `border-t border-border bg-background`.\n"
        "    • Inner: `container mx-auto flex flex-col gap-4 px-6 py-8 sm:flex-row sm:items-center sm:justify-between`.\n"
        "    • Left: brand monogram + small © year.\n"
        "    • Center (sm+): inline links from `landing.footer.links` (text-xs uppercase tracking-widest).\n"
        "    • Right: 3-4 small social icons.\n"
        "    • NO huge link grid; this is a quiet closer.\n"
    ),
    "cta-band-footer": (
        "ARCHETYPE: cta-band-footer (huge CTA panel above the footer — restaurants, conversion-focused).\n"
        "  ANATOMY:\n"
        "    • Outer wrapper has TWO bands.\n"
        "    • Band 1: `bg-primary text-primary-foreground py-16 lg:py-24`, centered.\n"
        "      Contains a giant headline (text-4xl md:text-5xl lg:text-6xl font-bold tracking-tight),\n"
        "      a subhead (text-lg max-w-2xl mx-auto), and a primary CTA pill (bg-background text-foreground).\n"
        "      Pull the headline from `landing.ctas?.primary?.label` + brand tagline if needed.\n"
        "    • Band 2: `border-t border-border bg-card py-10`. Inner: contact info + small link row + © line.\n"
        "    • Use this when the prompt is conversion-heavy (restaurant reservation, SaaS sign-up landing).\n"
    ),
    "centered-stack": (
        "ARCHETYPE: centered-stack (vertical stacked footer with brand statement — luxury / editorial).\n"
        "  ANATOMY:\n"
        "    • `<footer>` `bg-foreground text-background py-20`.\n"
        "    • Centered column max-w-2xl: brand name (very large text-4xl md:text-5xl font-bold), tagline\n"
        "      below in italic muted color, then a thin h-px w-12 bg-background/30 divider.\n"
        "    • Below: 4-6 inline links (text-xs uppercase tracking-[0.3em] gap-6).\n"
        "    • Below: social icon row centered.\n"
        "    • Bottom: small © line text-background/50.\n"
    ),
}


# Master map: section type → archetype library. When a section.type maps to an
# entry here, the user_prompt injects the matching archetype's anatomy block.
# Adding a new section variant = add a new entry to its library; no other
# code changes required.
_SECTION_ARCHETYPES: dict[str, dict[str, str]] = {
    "hero": _HERO_ARCHETYPES,
    "menu": _MENU_ARCHETYPES,
    "gallery": _GALLERY_ARCHETYPES,
    "testimonials": _TESTIMONIALS_ARCHETYPES,
    "features": _FEATURES_ARCHETYPES,
    "value_prop": _FEATURES_ARCHETYPES,  # alias — same anatomy library applies
    "benefits": _FEATURES_ARCHETYPES,
    "how_it_works": _FEATURES_ARCHETYPES,
    "process": _FEATURES_ARCHETYPES,
}


def _default_archetype(section_type: str) -> str:
    """First-defined archetype per section type — the safe fallback."""
    lib = _SECTION_ARCHETYPES.get(section_type.lower())
    if not lib:
        return ""
    return next(iter(lib.keys()))


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
) -> str:
    """Per-call system prompt — small, focused, no rules unrelated to a single section."""
    palette_lines = "\n".join(f"  --{k}: {v};" for k, v in palette.items())
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}
    pers_block = ""
    if pers:
        vibe = ", ".join(pers.get("vibe_keywords") or [])
        pers_block = (
            f"\nPERSONALITY (tune copy tone, color usage, and motion intensity to match):\n"
            f"  Tone: {pers.get('tone', 'confident')}\n"
            f"  Vibe keywords: {vibe or 'modern, clear'}\n"
            f"  Energy: {pers.get('energy', 'medium')}\n"
        )

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
1. The component reads its content from `@/content/landing.json` — never hardcode copy.
   Pattern:
     import landing from "@/content/landing.json";
     const section = landing.sections.find((s) => s.id === "<section-id>");
     // then render section.headline, section.subheadline, section.items, section.cta, section.images, etc.
2. Use TAILWIND CLASSES ONLY for styling. NEVER write inline `style={{...}}` for colors.
   Use semantic Tailwind tokens: bg-primary, text-foreground, bg-muted, border-border, bg-card, text-muted-foreground.
3. Render the section to match the provided layout_hint.
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
  Accent shape:     {ds.get("accent_shape", "rounded")}  → squared: rounded-none/sm; rounded: rounded-xl; pill: rounded-full on buttons + rounded-2xl on cards; blob: rounded-[40%_60%_70%_30%/40%_50%_60%_50%] on image masks; hairline: rounded-none + thin border accents.
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

DESIGN CONTEXT
  Brand: {brand_name}
  Motif: {motif}
  Heading font: {typography.get("heading_font", "Inter")}
  Body font:    {typography.get("body_font", "Inter")}
  Palette (already wired as CSS vars in globals.css):
{palette_lines}
{pers_block}{ref_block}

SECTION ARCHETYPES (driven by section.archetype + the user message)
  hero / menu / gallery / testimonials / features / value_prop / benefits / how_it_works / process —
  the user message will include an "ARCHETYPE — IMPLEMENT THIS EXACT ANATOMY" block. Follow it
  PRECISELY: structural skeleton (where copy/image/CTA go, grid shape, scale tokens) is non-negotiable.
  Tailwind class choices, copy positioning details, and decorative accents are yours. NEVER
  substitute a different layout pattern. Centered-text-on-flat-color = guaranteed FAILURE.

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
    Section MUST be at least `min-h-[480px]` content-wise — fill all 3 blocks before any spacer.

  experience / journey / process / steps — When the section uses a horizontal carousel of cards,
    EVERY card in the visible viewport MUST have the SAME height. Use `flex` on the track with
    `[&>*]:h-[420px] sm:[&>*]:h-[460px]` (or grid + `auto-rows-fr`). Each card uses
    `relative overflow-hidden rounded-2xl` with the photo as `<Image fill className="object-cover" />`
    INSIDE the card — never let an oversized image push a single card taller than its siblings.
    Bottom-align text via a `bg-gradient-to-t from-foreground/85 via-foreground/40 to-transparent`
    overlay with the title + body in `text-background` on the lower third.

PIXEL-PRECISE LAYOUT TOKENS (use exactly these — they keep the whole page on one rhythm)
  • Outer section: `<section id="..." className="<bg> py-20 md:py-28 lg:py-36">` — vertical rhythm is fixed.
  • Container: `<div className="container max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">` — never wider, never narrower.
  • Eyebrow:    `text-xs tracking-[0.2em] uppercase text-primary font-semibold mb-3`
  • H2:         `text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight font-[family-name:var(--font-heading)]`
  • Subhead:    `mt-4 text-lg md:text-xl text-muted-foreground max-w-2xl`  (max-w-3xl when centered)
  • Body copy:  `text-base md:text-lg text-muted-foreground leading-relaxed`
  • Section gap: between heading group and content block use `mt-12 md:mt-16 lg:mt-20`.
  • Card grid gap: `gap-4 md:gap-6 lg:gap-8` — never `gap-2` (cramped) or `gap-12` (too sparse).
  • Card chrome: `bg-card border border-border rounded-2xl p-6 lg:p-8 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg hover:border-primary/30`.
  • Button primary: `inline-flex items-center justify-center px-6 py-3 rounded-full bg-primary text-primary-foreground font-medium hover:opacity-90 transition`.
  • Button secondary: same but `bg-card border border-border text-foreground hover:bg-muted`.
  • Anti-stretching: NEVER let text run wider than `max-w-prose` (~65ch); NEVER let card columns exceed 4 on lg.

CONTRAST & READABILITY (NON-NEGOTIABLE — every line of text must be plainly legible)
  • Body / paragraph text MUST use `text-foreground` or `text-muted-foreground` — NEVER `text-foreground/40`,
    `text-muted-foreground/60`, or any sub-60% opacity for paragraph copy. Eyebrow tags and timestamps may use
    `text-muted-foreground` (full opacity) but never lower.
  • Headlines: ALWAYS `text-foreground` (or `text-primary-foreground` when sitting on `bg-primary`). Never apply opacity to headlines.
  • Text over IMAGES: stack a real overlay (`bg-foreground/60`, or `bg-gradient-to-t from-foreground/70 to-transparent`)
    BEFORE rendering text, then render text in `text-background`. Never put white text directly on unprocessed photos.
  • Text over `bg-primary`: must be `text-primary-foreground`. Text over `bg-card` / `bg-muted`: must be `text-foreground` (NOT muted).
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

SECTION SURFACE RHYTHM (forces the page to alternate, not look monotone)
  The user prompt for THIS section tells you which surface to use. Pick the OUTER section className
  from this menu — and pair it with the INNER card surface that has guaranteed contrast against it:

    Section bg = `bg-background`   → inner cards: `bg-card border border-border` (lighter pop).
    Section bg = `bg-muted/40`     → inner cards: `bg-background border border-border/60` (lighter pop).
    Section bg = `bg-card`         → inner cards: `bg-muted/40 border border-border` (subtle indent).
    Section bg = `bg-primary/5`    → inner cards: `bg-background border border-primary/20` (warm tint).
    Section bg = `bg-foreground`   → inner cards: `bg-background/10 border border-background/15` (dark mode panel).

  Required diversity rules:
    • NEVER ship two adjacent sections with the same outer bg. The user message lists the previous
      section's bg so you can pick a different one.
    • A section's inner card MUST always be a DIFFERENT shade than the section bg — never bg-card on
      bg-card, never bg-background on bg-background. Cards must POP off the section, not blend.
    • Form inputs (input, textarea, select) MUST use `bg-background border border-border` on a non-
      background section, or `bg-muted/40 border border-border` when the section IS bg-background.
      An input that visually disappears against its container is a FAILURE.

QUALITY BAR
  • Sections that are GENERIC (centered headline + 3 plain icon cards) are a FAILURE — every section must offer something visually distinct.
  • Hero must NOT be a centered text block on flat color — it must use a real background image with overlay.
  • Adjacent sections must visually differ (varying bg, layout, or rhythm). The user message tells you which background to use.
  • Cards have hover-lift + accent border or icon — never flat text-only.
  • Spacing follows the PIXEL-PRECISE LAYOUT TOKENS above.
  • DO NOT produce a section that is purely a paragraph of text — every section earns its place visually.
  • DO NOT default to a 3-column icon-card grid for non-photo sections — pick a NO-PHOTO LAYOUT VARIANT from the user message.
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
) -> str:
    """Per-section user prompt — section spec + 2 sibling specs for cohesion."""
    sib_summaries = []
    for sib in siblings:
        sib_summaries.append(
            f"  - id={sib.get('id')!r} type={sib.get('type')!r} layout={sib.get('layout_hint')!r} archetype={sib.get('archetype','')!r}"
        )
    sib_block = "\n".join(sib_summaries) or "  (none)"

    # Visual rhythm cue: rotate through a 4-bg cycle so adjacent sections
    # always differ AND the page reads as visually balanced (not just
    # alternating two colors). Hero (index 0) is the photo hero so we still
    # use bg-background under it. The cycle is tuned so primary/5 (warm
    # tint) appears once near the middle for visual anchor.
    _BG_CYCLE = ["bg-background", "bg-muted/40", "bg-card", "bg-primary/5"]
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

    # Archetype injection: when this section's type maps into the archetype
    # library, append the matching anatomy skeleton. Sections without a
    # library entry fall back to the OTHER SECTION RULES in the system prompt.
    archetype_block = ""
    section_type = (section.get("type") or "").lower()
    if section_type in _SECTION_ARCHETYPES:
        lib = _SECTION_ARCHETYPES[section_type]
        arch = (section.get("archetype") or "").strip().lower()
        if arch not in lib:
            arch = next(iter(lib.keys()))  # default to first variant
        anatomy = lib[arch]
        archetype_block = (
            f"\nARCHETYPE — IMPLEMENT THIS EXACT ANATOMY (the Brief picked {arch!r} for this {section_type} section):\n"
            f"{anatomy}\n"
            "DO NOT substitute a different layout pattern. Tailwind classes + copy positioning details are yours, "
            "but the structural skeleton above is the spec."
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

    return f"""Build ONE section component.

FILE PATH:     {file_path}
COMPONENT:     {component_name}
SECTION ID:    {section.get('id')}
SECTION TYPE:  {section.get('type')}
LAYOUT HINT:   {section.get('layout_hint')}
ROLE:          {section.get('role','')}
POSITION:      section {section_index + 1} of {section_count}.
SECTION BG:    `{bg_hint}` — use this on the outer <section>. Previous section was `{prev_bg_hint}`, so DO NOT
               repeat that surface. Pair the bg with inner-card surfaces per the SECTION SURFACE RHYTHM rules.

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
{image_hint}{archetype_block}{interactivity_block}

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
    )
    usr_p = _user_prompt(
        section, siblings, component, file_path,
        section_index=section_index, section_count=section_count,
        voice_context=voice_context,
    )

    try:
        result = await call_claude_for_json(
            system_prompt=sys_p,
            user_prompt=usr_p,
            api_key=api_key,
            websocket=websocket,
            max_tokens=_SECTION_MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("section %s: codegen exception — %s", section.get("id"), exc)
        return None

    if not result or "files" not in result:
        logger.warning("section %s: empty result from Claude", section.get("id"))
        return None

    files = result.get("files") or []
    if not files:
        logger.warning("section %s: Claude returned 0 files", section.get("id"))
        return None

    # Claude was asked for one file — take the first JSX/TSX.
    for f in files:
        path = (f.get("path") or "").strip()
        content = (f.get("content") or "")
        if path.endswith((".jsx", ".tsx")) and content:
            # Force the path to our canonical location so Claude can't pick a different folder
            return {"path": file_path, "content": content}

    logger.warning("section %s: no .jsx/.tsx file in Claude result", section.get("id"))
    return None


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

    # Voice/context fields populated by enrich_brief_with_signals when the
    # research stage succeeded. Absent on briefs built without research,
    # in which case the user prompt skips the VOICE & CONTEXT block.
    voice_context = {
        "voice_phrases":  brief.get("voice_phrases") or [],
        "industry_terms": brief.get("industry_terms") or [],
        "regional_refs":  brief.get("regional_refs") or [],
        "white_space":    brief.get("white_space") or [],
    }

    sem = asyncio.Semaphore(concurrency)

    total = len(sections)
    async def _bounded(idx: int, s: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str] | None]:
        async with sem:
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
            )
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
        return_exceptions=False,
    )
    # Restore original order
    results.sort(key=lambda x: x[0])

    sections_meta: list[dict[str, Any]] = []
    page_imports: list[str] = []
    page_renders: list[str] = []

    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    os.makedirs(sections_dir, exist_ok=True)

    for _, section, res in results:
        filename = _section_filename(section)
        component = _component_name(filename)
        file_path = f"src/components/sections/{filename}"
        ok = bool(res)

        if ok:
            disk_path = os.path.join(workspace_path, file_path)
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(res["content"])
            page_imports.append(
                f'import {component} from "@/components/sections/{component}";'
            )
            page_renders.append(f"<{component} />")
        else:
            logger.warning("section %s (%s): codegen FAILED — skipping", section.get("id"), section.get("type"))

        sections_meta.append({
            "id": section.get("id"),
            "type": section.get("type"),
            "file_path": file_path,
            "component": component,
            "ok": ok,
        })

    if websocket is not None:
        ok_count = sum(1 for m in sections_meta if m["ok"])
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ {ok_count}/{len(sections_meta)} sections generated",
            })
        except Exception:
            pass

    return {
        "sections": sections_meta,
        "page_imports": page_imports,
        "page_renders": page_renders,
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
) -> str:
    """System prompt for header/footer codegen — anatomy-driven, JSON-fed."""
    palette_lines = "\n".join(f"  --{k}: {v};" for k, v in palette.items())
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}
    vibe = ", ".join(pers.get("vibe_keywords") or [])
    pers_block = (
        f"\nPERSONALITY (tone the layout to match this voice):\n"
        f"  Tone: {pers.get('tone', 'confident')}\n"
        f"  Vibe: {vibe or 'modern, clear'}\n"
        f"  Energy: {pers.get('energy', 'medium')}\n"
    )
    ref_block = ""
    if references:
        rls = []
        for r in references[:3]:
            n = r.get("name") or r.get("url", "")
            why = r.get("why", "")
            rls.append(f"  • {n} — {why}")
        if rls:
            ref_block = "\nREFERENCE SITES (real sites this brief is grounded in):\n" + "\n".join(rls) + "\n"

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
2. Use TAILWIND CLASSES ONLY. NEVER inline `style={{}}` for colors. Use bg-primary / text-foreground / bg-muted / border-border / bg-card / text-muted-foreground.
3. Default-export a React function named `{component_name}` (matching filename).
4. Mark `'use client';` as the FIRST line if you use useState / useEffect / onClick.
5. Lucide-react icons for social (Instagram, Twitter, Facebook, Linkedin, Youtube, Github) and any UI affordances (Menu, X, ChevronDown). Map social.label string → icon via a small const dict.
6. NO CSS modules, NO styled-components, NO dynamic class strings Tailwind can't parse.

ARCHETYPE — IMPLEMENT THIS EXACT ANATOMY:
{anatomy}
DO NOT substitute a different layout pattern. Tailwind class choices and decorative details are yours, but the structural skeleton is the spec.

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
{pers_block}{ref_block}

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
) -> dict[str, str] | None:
    from app.services.project_generator import call_claude_for_json

    component = "MarketingHeader" if kind == "header" else "MarketingFooter"
    file_path = f"src/components/layout/{component}.jsx"

    sys_p = _layout_system_prompt(
        component, file_path, archetype_label, anatomy,
        brand_name, motif, palette, typography, design_system, personality, references,
        design_tokens=design_tokens,
    )
    usr_p = _layout_user_prompt({}, component, file_path)

    try:
        result = await call_claude_for_json(
            system_prompt=sys_p,
            user_prompt=usr_p,
            api_key=api_key,
            websocket=websocket,
            max_tokens=_SECTION_MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("layout %s: codegen exception — %s", kind, exc)
        return None

    if not result or not result.get("files"):
        logger.warning("layout %s: empty result from Claude", kind)
        return None

    for f in result["files"]:
        path = (f.get("path") or "").strip()
        content = (f.get("content") or "")
        if path.endswith((".jsx", ".tsx")) and content:
            return {"path": file_path, "content": content}

    logger.warning("layout %s: no .jsx/.tsx file in Claude result", kind)
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
    brand_name = (brief.get("brand") or {}).get("name", "")
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

    h_arch = (brief.get("header_archetype") or "solid-bar").strip().lower()
    f_arch = (brief.get("footer_archetype") or "minimalist-row").strip().lower()
    if h_arch not in _HEADER_ARCHETYPES:
        h_arch = "solid-bar"
    if f_arch not in _FOOTER_ARCHETYPES:
        f_arch = "minimalist-row"

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"⚡ Generating header ({h_arch}) + footer ({f_arch}) in parallel...",
            })
        except Exception:
            pass

    header_task = _generate_layout_component(
        kind="header",
        archetype_label=h_arch,
        anatomy=_HEADER_ARCHETYPES[h_arch],
        brand_name=brand_name, motif=motif, palette=palette, typography=typography,
        design_system=design_system, personality=personality, references=references,
        design_tokens=design_tokens,
        api_key=api_key, websocket=websocket,
    )
    footer_task = _generate_layout_component(
        kind="footer",
        archetype_label=f_arch,
        anatomy=_FOOTER_ARCHETYPES[f_arch],
        brand_name=brand_name, motif=motif, palette=palette, typography=typography,
        design_system=design_system, personality=personality, references=references,
        design_tokens=design_tokens,
        api_key=api_key, websocket=websocket,
    )

    header_res, footer_res = await asyncio.gather(header_task, footer_task)

    layout_dir = os.path.join(workspace_path, "src", "components", "layout")
    os.makedirs(layout_dir, exist_ok=True)

    out: dict[str, bool] = {"header": False, "footer": False}

    for kind, res in (("header", header_res), ("footer", footer_res)):
        if not res:
            logger.warning("layout %s: codegen FAILED — pipeline will fall back to deterministic stub", kind)
            continue
        disk = os.path.join(workspace_path, res["path"])
        os.makedirs(os.path.dirname(disk), exist_ok=True)
        with open(disk, "w", encoding="utf-8") as fh:
            fh.write(res["content"])
        out[kind] = True
        logger.info("layout %s: wrote %s (%d bytes)", kind, disk, len(res["content"]))

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


def write_landing_page_shell(workspace_path: str, page_imports: list[str], page_renders: list[str]) -> str:
    """Write `app/page.jsx` that imports + renders all section components in order.

    The shell is dead-simple — no header/footer here (those are layout-level
    components written by Phase 0 builders). Sections render top-to-bottom.
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

    target = os.path.join(app_dir, "page.jsx")
    imports_block = "\n".join(page_imports)
    renders_block = "\n      ".join(page_renders) if page_renders else "<div />"
    src = f"""{imports_block}

export default function Page() {{
  return (
    <main className="min-h-screen bg-background text-foreground">
      {renders_block}
    </main>
  );
}}
"""
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(src)
    logger.info("write_landing_page_shell: wrote %s with %d sections", target, len(page_renders))
    return target
