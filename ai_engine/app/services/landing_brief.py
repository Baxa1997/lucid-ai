"""Landing-page Brief — grounded research + structured distill.

Three Gemini calls per Brief:
  1. STRUCTURE_RESEARCH  (gemini-2.5-pro, google_search grounding)
       Reads 4-6 real reference sites for the user's domain and reports
       common section orders, page-level structures, and notable
       interactive features. Output: free-text research dump.
  2. DESIGN_DNA_RESEARCH (gemini-2.5-pro, google_search grounding)
       From the same domain, extracts the visual design language
       (palette HSL, typography, motif, motion, image treatment) used
       by best-in-class real sites. Output: free-text dump.
  3. DISTILL              (gemini-2.5-flash, structured JSON output)
       Fuses both research dumps + user prompt into the Brief schema.

Calls 1 + 2 run in parallel (asyncio.gather). Total wall time ~40-60s.
This is the change requested when the user said "research real projects
and get the structure from them ... in parallel get design pattern
variables to give to claude for coding."

If grounded research fails (no key, timeout, model error), each call
falls back to ungrounded text generation so the pipeline keeps working
even without web access.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Any

import httpx

# Model IDs are env-overridable so we can swap without code changes.
# Pro for grounded research (better tool-use + citation handling).
# Flash for the distill — small structured-output task, no tools.
_RESEARCH_MODEL = os.environ.get("LANDING_RESEARCH_MODEL", "gemini-3.1-pro-preview")
_BRIEF_MODEL = os.environ.get("LANDING_BRIEF_MODEL", "gemini-3-flash-preview")

logger = logging.getLogger(__name__)


# ── Structured-output schema (Gemini responseSchema) ─────────────────
_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["brand", "palette", "typography", "motif", "design_system", "sections", "ctas", "domain_keywords"],
    "properties": {
        "brand": {
            "type": "OBJECT",
            "required": ["name", "tagline", "description", "domain"],
            "properties": {
                "name": {"type": "STRING"},
                "tagline": {"type": "STRING"},
                "description": {"type": "STRING"},
                "domain": {"type": "STRING"},
                "business_info": {
                    "type": "OBJECT",
                    "properties": {
                        "address": {"type": "STRING"},
                        "phone": {"type": "STRING"},
                        "email": {"type": "STRING"},
                        "hours": {"type": "STRING"},
                    },
                },
                "social": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "label": {"type": "STRING"},
                            "href": {"type": "STRING"},
                        },
                    },
                },
            },
        },
        "palette": {
            "type": "OBJECT",
            "required": ["primary", "secondary", "accent", "background", "foreground", "muted", "border", "card"],
            "properties": {
                "primary": {"type": "STRING"},
                "secondary": {"type": "STRING"},
                "accent": {"type": "STRING"},
                "background": {"type": "STRING"},
                "foreground": {"type": "STRING"},
                "muted": {"type": "STRING"},
                "border": {"type": "STRING"},
                "card": {"type": "STRING"},
            },
        },
        "typography": {
            "type": "OBJECT",
            "required": ["heading_font", "body_font"],
            "properties": {
                "heading_font": {"type": "STRING"},
                "body_font": {"type": "STRING"},
                "scale": {"type": "STRING"},
            },
        },
        "motif": {"type": "STRING"},
        "design_system": {
            "type": "OBJECT",
            "required": ["motion", "accent_shape", "surface", "image_treatment", "section_rhythm"],
            "properties": {
                "motion":          {"type": "STRING"},
                "accent_shape":    {"type": "STRING"},
                "surface":         {"type": "STRING"},
                "image_treatment": {"type": "STRING"},
                "section_rhythm":  {"type": "STRING"},
            },
        },
        "header_archetype": {"type": "STRING"},
        "footer_archetype": {"type": "STRING"},
        "personality": {
            "type": "OBJECT",
            "properties": {
                "tone":          {"type": "STRING"},
                "vibe_keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
                "energy":        {"type": "STRING"},
            },
        },
        "references": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "url":     {"type": "STRING"},
                    "name":    {"type": "STRING"},
                    "why":     {"type": "STRING"},
                    "section_order": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "notable_features": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
            },
        },
        "sections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["id", "type", "role", "headline", "layout_hint"],
                "properties": {
                    "id": {"type": "STRING"},
                    "type": {"type": "STRING"},
                    "role": {"type": "STRING"},
                    "nav_label": {"type": "STRING"},
                    "headline": {"type": "STRING"},
                    "subheadline": {"type": "STRING"},
                    "body": {"type": "STRING"},
                    "layout_hint": {"type": "STRING"},
                    "archetype": {"type": "STRING"},
                    "interactivity": {"type": "STRING"},
                    "items": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "title": {"type": "STRING"},
                                "description": {"type": "STRING"},
                                "icon": {"type": "STRING"},
                                "value": {"type": "STRING"},
                                "label": {"type": "STRING"},
                                "image_query": {"type": "STRING"},
                                "details": {"type": "STRING"},
                            },
                        },
                    },
                    "cta": {
                        "type": "OBJECT",
                        "properties": {
                            "label": {"type": "STRING"},
                            "href": {"type": "STRING"},
                        },
                    },
                    "image_queries": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
            },
        },
        "ctas": {
            "type": "OBJECT",
            "required": ["primary"],
            "properties": {
                "primary": {
                    "type": "OBJECT",
                    "required": ["label", "href"],
                    "properties": {
                        "label": {"type": "STRING"},
                        "href": {"type": "STRING"},
                    },
                },
                "secondary": {
                    "type": "OBJECT",
                    "properties": {
                        "label": {"type": "STRING"},
                        "href": {"type": "STRING"},
                    },
                },
            },
        },
        "domain_keywords": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
}


# ── Research prompts (parallel grounded calls) ──────────────────────────

_STRUCTURE_RESEARCH_PROMPT = """You are a senior product researcher. USE GOOGLE SEARCH to study REAL websites for this prompt. Do NOT rely on training memory — actually search.

PROMPT: "{description}"
DOMAIN: {domain}

YOUR JOB — research the structure of real, current, best-in-class sites in this domain:

1. Search for 4-6 reference websites that match the prompt domain (e.g. for "Brooklyn restaurant" → search for award-winning Brooklyn restaurant sites; for "B2B logistics SaaS" → search for top logistics platforms).

2. For each reference, list:
   • URL + name
   • SECTION ORDER as it appears on their HOMEPAGE (e.g. hero → philosophy → menu → reservations → gallery → press → footer)
   • For multi-page sites: the key OTHER pages (about, menu, reservations, contact) and 1-line of what each page contains
   • NOTABLE INTERACTIVE FEATURES (e.g. "menu items open a modal with photo + ingredients", "sticky reservation widget bottom-right", "lightbox gallery with keyboard nav", "animated stat counters on scroll", "filter pills for menu categories")
   • Why this site is a good reference (1 sentence)

3. CONCLUSION: synthesize a recommended 7-section blueprint for THIS prompt by picking from the patterns common across references. List the section types in order, why each is included, and which interactive feature each section should have.

OUTPUT FORMAT — plain markdown, no JSON yet. Use these section headers:

===REFERENCES===
1. <name> — <url>
   Sections: hero → ... → footer
   Pages: home, /menu (...), /reservations (...), /about (...)
   Features: ...
   Why: ...
2. ...

===CONCLUSION===
Recommended sections (7 total, hero first, no footer — picked from patterns above):
1. hero — <archetype hint based on what references do>
2. <type> — <archetype hint> — <interactivity>
... 7 total ...
Recommended header style: <transparent-pill | solid-bar | centered-logo | side-rail | mega-menu>
Recommended footer style: <mega-columns | minimalist-row | cta-band-footer | centered-stack>
"""


_DESIGN_DNA_RESEARCH_PROMPT = """You are a senior brand & UI designer.

⚠️ SEARCH-FIRST: Before writing ANYTHING, run google_search at least 2 times. This prompt is invalid if your answer comes from training memory. The URLs you cite in ===REFERENCES=== below MUST come from search results you just saw — never invent or recall a URL.

Recommended initial searches (run AT LEAST these two):
  1. "best {domain} websites 2025" OR "{domain} website awwwards"
  2. specific search drawn from the prompt itself, e.g. for a prompt mentioning brands or cities, search those terms directly.

PROMPT: "{description}"
DOMAIN: {domain}

YOUR JOB — extract concrete design DNA from real best-in-class sites in this domain:

1. Search for 4-6 reference sites in this domain (use awwwards / google for "<domain> website 2025" or specific brand names).

2. For each reference, record:
   • Color palette: 3-5 dominant HSL values (look at their actual brand colors). Use formula "H S% L%".
   • Typography: heading font + body font (use exact Google Fonts names if recognizable, e.g. "Playfair Display", "Söhne", "Inter", "Cormorant Garamond"). If non-Google, name the closest Google Fonts equivalent.
   • Motif word: minimal | editorial | warm | tech | bold | organic | playful | serious | luxe
   • Motion: subtle | energetic | dramatic | organic
   • Image treatment: natural | overlay | duotone | masked | bordered
   • Accent shape: squared | rounded | pill | blob | hairline

3. CONCLUSION: synthesize ONE design language for the prompt that combines the strongest patterns across references. Include:
   • palette (8 HSL slots: primary, secondary, accent, background, foreground, muted, border, card)
   • typography (heading_font, body_font from real Google Fonts only)
   • motif (one word)
   • design_system (motion, accent_shape, surface, image_treatment, section_rhythm)
   • personality (tone, 3-5 vibe_keywords, energy: low/medium/high)

OUTPUT FORMAT — plain markdown, no JSON. Headers:

===REFERENCES===  (must contain ≥3 distinct REAL URLs from search results)
1. <name> — <url>
   Palette: primary <H S L>, accent <H S L>, ...
   Typography: heading "..." / body "..."
   Motif: ...
   Motion: ...
   Image: ...

===CONCLUSION===
Palette:
  primary:    H S% L%
  secondary:  H S% L%
  accent:     H S% L%
  background: H S% L%
  foreground: H S% L%
  muted:      H S% L%
  border:     H S% L%
  card:       H S% L%
Typography: heading="...", body="..."
Motif: ...
Design system:
  motion:          ...
  accent_shape:    ...
  surface:         ...
  image_treatment: ...
  section_rhythm:  ...
Personality:
  tone: <single word — e.g. encouraging | confident | playful | luxurious>
  vibe_keywords: [3-5 words]
  energy: <low | medium | high>
"""


_DISTILL_PROMPT = """You are briefing an engineer to build ONE landing page. You have TWO research dumps from real reference sites. Your job: distill them into a SINGLE structured Brief for this project.

PROJECT: "{description}"
DOMAIN: {domain}

===STRUCTURE_RESEARCH===
{structure_research}

===DESIGN_DNA_RESEARCH===
{design_dna_research}

NOW produce the Brief as a JSON object matching the response schema EXACTLY. No prose outside the JSON.

REQUIREMENTS — every string field has a character limit, OBEY THEM.

brand: name (real, ≤30 chars); tagline (≤60 chars); description (≤140 chars); domain (≤30 chars).
  business_info (encouraged for local businesses): address ≤80, phone ≤24, email ≤60, hours ≤200 (multi-line ok).
  social (0-5 entries): each {{label: "instagram"|"facebook"|"twitter"|"linkedin"|"youtube", href: URL}}.

palette: 8 HSL strings ("220 90% 56%" — no hsl(), no commas) — USE the palette from DESIGN_DNA_RESEARCH conclusion. Keys: primary, secondary, accent, background, foreground, muted, border, card.

typography: USE the heading_font/body_font from DESIGN_DNA_RESEARCH conclusion. Real Google Fonts only. scale: tight|balanced|expressive.

motif: ONE word from DESIGN_DNA_RESEARCH conclusion.

design_system: USE the design_system from DESIGN_DNA_RESEARCH conclusion. All 5 fields required.

personality: USE the personality block from DESIGN_DNA_RESEARCH. tone, vibe_keywords (3-5), energy (low|medium|high).

header_archetype: pick ONE from [transparent-pill, solid-bar, centered-logo, side-rail, mega-menu] — match what STRUCTURE_RESEARCH recommended.
  STRONG BIAS — pick a NON-`solid-bar` archetype unless the domain is plainly B2B SaaS / corporate. `solid-bar` is the
  generic default and produces a forgettable white nav. Use:
    • `transparent-pill`  → hotels, restaurants, hospitality, lifestyle, beauty, wellness, travel, fashion (premium / floating glass).
    • `centered-logo`     → fine dining, fashion, jewelry, editorial, luxury (heritage / serif feel).
    • `side-rail`         → portfolios, agencies, studios, photography (vertical magazine).
    • `mega-menu`         → marketplaces, large e-com, multi-category platforms.
    • `solid-bar`         → ONLY for plain B2B SaaS, dev tools, fintech, operational dashboards.
  When in doubt between two non-default options, let DESIGN_SEED pick — never default to solid-bar out of caution.

footer_archetype: pick ONE from [mega-columns, minimalist-row, cta-band-footer, centered-stack] — match what STRUCTURE_RESEARCH recommended.

references: copy 3-5 entries from STRUCTURE_RESEARCH ===REFERENCES===. Each: {{url, name, why (≤120 chars), section_order (5-10 strings), notable_features (2-4 strings)}}.

sections: ARRAY of 7 OBJECT items, FIRST type "hero", in order. Pick types from the STRUCTURE_RESEARCH conclusion. NO footer in this array (added downstream). Allowed types: features, how_it_works, pricing, testimonials, faq, stats, gallery, menu, integrations, comparison, team, contact, contact_form, reservation, booking_form, newsletter, cta, process, benefits, value_prop, locations, story, philosophy, press, hours.

  At most ONE form-style section (pick exactly one of contact_form, reservation, booking_form, newsletter).
  Restaurant/food MUST include `menu` + `gallery`.
  At least one of: gallery, stats, testimonials.

For EVERY section, populate:
  id (≤24 chars, kebab-case, unique); type (≤24 chars, snake_case);
  role (≤60 chars);
  nav_label — REQUIRED. ONE or TWO short words for the marketing header link (max 14 chars).
    Must read like a real nav item: "Stays" not "Accommodations Showcase", "Menu" not "Menu Photo Card Grid",
    "Story" not "About Philosophy", "Reviews" not "Social Proof Testimonial", "Book" not "Direct Booking Incentive",
    "Gallery" not "Gallery Section", "Experiences" not "Experiences Hook". Single nouns are best.
    NEVER repeat the section type verbatim, NEVER include the words "section", "hook", "showcase", "incentive",
    "testimonial" (use "Reviews"), "philosophy" (use "Story" or "About"). For hero/footer/cta/newsletter,
    omit nav_label (they don't appear in nav).
  headline (≤80 chars);
  subheadline (≤140 chars, optional, ONE sentence);
  layout_hint — centered-stack|two-column|split-image-left|split-image-right|grid-3|grid-4|grid-2|carousel|accordion|logo-strip|stat-band|timeline|comparison-table|media-quote;
  archetype — REQUIRED for these types: hero, menu, gallery, testimonials, features, value_prop, benefits, how_it_works, process. Pick from the per-type list:
    hero: full-bleed-overlay | oversized-watermark | asymmetric-split | type-wrapping-product | video-mask | card-stack
    menu: two-column-dotted | photo-card-grid | categorized-rows
    gallery: asymmetric-12col | marquee-scroll | bento-mosaic
    testimonials: glass-cards-bg | marquee-row | big-quote-portrait
    features/value_prop/benefits/how_it_works/process: icon-grid-3 | numbered-stepper | split-image-bullets
  interactivity — REQUIRED. ONE concrete interactive behavior the section MUST have. Examples: "menu items open a modal with full description and photo", "lightbox gallery with prev/next keyboard nav", "FAQ accordion with smooth open/close", "testimonials carousel with autoplay + manual arrows", "pricing toggle: monthly/yearly", "smooth-scroll CTA to #reservations". Tie this to STRUCTURE_RESEARCH features when possible.
  items: ARRAY of 3-6 objects when relevant (each ≤120-char description, optional `details` string ≤200 chars for modals).
    For VISUAL items (destinations, hotels, dishes, products, rooms, properties, people, events, packages),
    EVERY item MUST include an `image_query` field — a 3-6 word Unsplash search like "kyoto japan temple sunset"
    or "boutique hotel paris balcony" or "boudin sourdough bread plated". Without this, downstream codegen
    falls back to generic icon cards (visually identical across sections) — exactly what we are NOT shipping.
  cta: {{label ≤24, href "#anchor"}};
  image_queries: ARRAY of strings — Unsplash queries used directly by the section.
    HERO: exactly 1.
    gallery: 4-6.
    PHOTO-CARD SECTIONS (destinations, menu with photo-card-grid, featured_listings, properties, products, rooms,
      hotels, popular_destinations, team_with_portraits): one query PER item, parallel-indexed with section.items.
      So if items has 6 destinations, image_queries has 6 location queries in the same order.
    Other sections: 0-1 queries (background or accent only).

  ── PER-SECTION CONTENT DENSITY (every section must feel COMPLETE, not sparse) ──
  Every non-hero section MUST contain ≥3 distinct content blocks (heading group +
  primary visual/list + supporting block). Sparse sections kill the page. Type minimums:

    press / awards / publications →
      DO NOT write a free-prose body paragraph. Instead:
        • headline: ≤80 chars (e.g. "Recognized for excellence")
        • subheadline: ONE plain sentence ≤140 chars, NO inline placeholders, NO pill words.
        • items (REQUIRED, 3-5): each {{title: <real publication / award / outlet name>,
          description: <8-16-word actual quote or accolade with attribution>,
          value: <year or rating, optional>, label: <pub name verbatim>}}
        Examples of REAL items:
          - title: "Eater NY", description: "\"A masterclass in the Spanish small-plate canon.\""
          - title: "James Beard Foundation", description: "Semifinalist, Best New Restaurant 2024"
          - title: "The Infatuation", description: "\"Worth the wait — and the splurge.\""
        NEVER write items whose title is an adjective ("authentic", "passionate") — title is
        always a publication/award NAME. NEVER include image_queries for press; render as text.

    menu / dishes / featured_dishes →
      items REQUIRED, 4-8: each {{title: dish name, description: 12-22-word ingredient
      sentence, value: price string like "$18" if relevant, image_query: cuisine + dish}}.
      image_queries: parallel to items, one query per dish.

    testimonials / reviews →
      items REQUIRED, 3-6: each {{title: <reviewer real name>, description: 18-30-word direct
      quote, label: <reviewer role / location / "Yelp" / "Google" / publication name>,
      value: <rating "5.0" if relevant>}}.

    features / benefits / value_prop →
      items REQUIRED, 3-6: each {{title: 2-5-word capability, description: 15-25-word concrete
      benefit (NOT generic "we do it well"), icon: lucide icon name}}.

    gallery →
      image_queries REQUIRED, 4-8 distinct subject queries (no duplicates).
      items optional: short captions parallel to image_queries.

    stats →
      items REQUIRED, 3-4: each {{value: number+unit ("12 yrs" / "5,000+"), label: 2-4 words}}.

    faq →
      items REQUIRED, 5-8: each {{title: question, description: 1-3-sentence answer}}.

    process / how_it_works / experience / journey →
      items REQUIRED, 3-5: each {{title: step name, description: 10-20-word what-happens,
      value: step number "01"|"02"|...}}.

    locations / contact / hours →
      Use brand.business_info — no pill-cluster prose.

    ── HIRING / RECRUITMENT SECTIONS (when primary_purpose is hiring) ──
    These section types appear when the page recruits candidates. Treat
    image_queries the way you'd treat any other visual section — empty
    queries here are why hiring pages currently render as text walls.

    hero (when purpose=hiring) →
      image_queries REQUIRED, 1: a SUBJECT noun rooted in the work itself.
      For drivers: "freight truck highway sunset" / "cdl driver smiling cab".
      For restaurant kitchen: "line cook plating busy kitchen".
      For nurses: "nurse hospital corridor smiling".
      Show the WORK or the WORKER, not a generic office.

    open_roles / open_positions / job_openings →
      items REQUIRED, parallel to named_roles when known: each
        {{title: role name (e.g. "CDL-A Driver"),
         description: 12-20-word duty / route / shift summary,
         value: pay range (e.g. "$0.65-0.78 CPM" or "$65k-90k/yr"),
         label: shift type / route type / location}}.
      image_queries: 0 (cards render with icon + text, not photos).

    compensation / pay / benefits →
      items REQUIRED, 3-6: each {{title: benefit name (e.g. "Health Insurance",
        "Sign-On Bonus"), description: 12-20-word concrete detail,
        value: dollar / day / mile figure when relevant}}.
      image_queries: 0.

    requirements / qualifications →
      items REQUIRED, 4-8 short bullet strings (each ≤80 chars). Each
      is one requirement (CDL class, years exp, clean MVR, etc.).
      image_queries: 0.

    culture / why_work_here / day_in_life →
      image_queries REQUIRED, 2-4: real-work imagery (truck cab interior,
      driver lounge, fleet garage, route map detail).
      items optional: 2-4 short culture pillars.

    testimonials_employees / driver_voices →
      items REQUIRED, 3-5: each {{title: employee name, description: 18-30-word
      direct quote about the work / equipment / home time, label: role+tenure
      e.g. "OTR Driver, 4 yrs"}}.
      image_queries: 3-5 driver portraits when employees aren't real people
      (otherwise leave images empty and let the brand supply real photos).

    application_form / apply →
      image_queries: 0. Render as a working form, not a banner.

  ── BANNED COPY PATTERNS (do not write these in headline / subheadline / body / item.description) ──
  • CURLY-BRACE PLACEHOLDERS — NEVER write copy with `{{adjective}}` / `{{noun}}` inline,
    e.g. "Featured in {{culinary publications}} and praised for our {{passionate craft}}."
    These render in the UI as awkward pill chips inside paragraphs and look like un-filled
    template stubs. Write FINISHED prose with concrete subjects: "Featured in Eater NY and
    The Infatuation, and praised for hand-cured charcuterie and house-pressed olive oil."
  • TRAILING ELLIPSIS — never end body/description with "…". Finish the sentence.
  • EMPTY-PROMISE ADJECTIVES as the entire item title — no item.title equal to
    "Authentic", "Passionate", "Inviting" alone. Always pair with a noun: "Authentic Tapas",
    "Passionate Craft", "Inviting Room".

  ── HARD RULES FOR EVERY image_query (subject specificity) ────────────────
  1. EVERY query MUST contain at least ONE concrete SUBJECT noun from the
     brand's domain. Generic words alone ("interior", "ambience", "concept",
     "experience", "story", "philosophy") return random stock photos
     unrelated to the brand. INSTEAD include the subject:
       Japanese restaurant → "sushi counter omakase chef", "sashimi platter dark wood"
       Italian restaurant  → "pasta plate rustic table", "italian dining room candle"
       Hotel              → "luxury suite bed window view", "hotel pool sunset palms"
       Coffee shop        → "espresso machine barista", "latte art wood counter"
       Bakery             → "sourdough loaf flour", "patisserie display pastries"
       Yoga studio        → "yoga mat sunlight studio", "meditation pose wood floor"
  2. NEVER include the brand name verbatim in image_query. Unsplash does not
     index brand names — "Omakase Brooklyn interior" returns clothing stores
     called Brooklyn-X. Use the cuisine/category instead: "omakase japanese
     restaurant interior".
  3. NEVER use abstract single words ("concept", "philosophy", "vision",
     "story", "intention", "press") as the entire query. Always pair an
     abstract word with the literal subject:
       BAD  → "concept", "philosophy", "press logos"
       GOOD → "japanese kitchen prep counter", "chef hands rolling sushi",
              "newspaper article cropped"
  4. Restaurants — for chef portraits, include cuisine ethnicity context:
     "japanese chef portrait sushi" not "chef portrait" (otherwise Unsplash
     returns generic Western chefs). Same for "italian chef pizza", etc.
  5. Press / logos sections — image_queries should be empty []; the section
     renders text/logo placeholders, not photos.

DESIGN_SEED: {design_seed} — if unsure between two equally-good choices, let this seed nudge you toward the less-common one.

ctas: primary {{label, href}} required.
domain_keywords: 4-6 short Unsplash search strings.

HARD CONSTRAINTS
- Plain ASCII / natural unicode — no \\u escapes.
- 7 sections (1 hero + 6 others, no footer).
- Real specific copy in brand voice.
- archetype + interactivity REQUIRED on every section that takes them.
"""


# ── Public entry point ─────────────────────────────────────────────────

async def _emit_fallback_warning(websocket, reason: str) -> None:
    """Surface fallback-brief usage to the user.

    Without this, when Gemini is unreachable (bad ADC, quota, etc.) the
    pipeline silently produces a placeholder page (\"Built for what's next\")
    and the user has no idea their AI calls aren't actually firing.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "warning",
            "code": "FALLBACK_BRIEF",
            "message": (
                f"⚠️ AI research unavailable ({reason}). "
                "Generating a placeholder page — check ai_engine logs for the real error "
                "(usually ADC credentials or Vertex AI quota)."
            ),
        })
    except Exception:
        pass


async def build_landing_brief(
    description: str,
    classification: dict | None = None,
    *,
    websocket: Any = None,
    timeout_s: float = 240.0,
) -> dict[str, Any]:
    """Three-stage Brief: parallel grounded research → structured distill.

    Returns a dict matching ``_BRIEF_SCHEMA``. Falls back to a minimal
    Brief on any unrecoverable failure so downstream code never sees None.
    Auth is handled inside ``gemini_post`` via Vertex ADC.
    """
    classification = classification or {}
    domain = (classification.get("domain") or "general").strip()

    design_seed = random.randint(1000, 9999)

    # ── Stage 1+2: parallel grounded research ────────────────────────
    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "🔎 Researching real reference sites + design DNA in parallel...",
            })
        except Exception:
            pass

    structure_prompt = _STRUCTURE_RESEARCH_PROMPT.format(description=description.strip(), domain=domain)
    design_prompt = _DESIGN_DNA_RESEARCH_PROMPT.format(description=description.strip(), domain=domain)

    structure_text, design_text = await asyncio.gather(
        _grounded_research(structure_prompt, timeout_s, label="structure_research", websocket=websocket),
        _grounded_research(design_prompt, timeout_s, label="design_dna_research", websocket=websocket),
    )

    # If both research calls failed completely we have nothing to distill;
    # fall through to the legacy ungrounded distill so the pipeline still
    # produces a Brief.
    if not structure_text and not design_text:
        logger.warning("build_landing_brief: both research calls returned empty — distilling without research context")
        structure_text = "(research unavailable)"
        design_text = "(research unavailable)"

    # ── Stage 3: distill into structured Brief ───────────────────────
    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "📋 Distilling research into landing brief...",
            })
        except Exception:
            pass

    distill_prompt = _DISTILL_PROMPT.format(
        description=description.strip(),
        domain=domain,
        structure_research=(structure_text or "(none)")[:8000],
        design_dna_research=(design_text or "(none)")[:8000],
        design_seed=design_seed,
    )

    raw = await _structured_distill(distill_prompt, timeout_s)
    if not raw:
        logger.warning("build_landing_brief: distill returned empty — fallback brief")
        await _emit_fallback_warning(websocket, "Gemini brief call returned empty")
        return _fallback_brief(description, domain)

    try:
        brief = json.loads(raw)
    except json.JSONDecodeError as exc:
        salvaged = _try_salvage_truncated_json(raw)
        if salvaged is not None:
            logger.warning(
                "build_landing_brief: salvaged truncated JSON — %d sections",
                len(salvaged.get("sections") or []),
            )
            brief = salvaged
        else:
            logger.warning("build_landing_brief: JSON parse failed (%s) — fallback. Head: %s",
                           exc, raw[:200])
            await _emit_fallback_warning(websocket, f"Brief JSON parse failed: {str(exc)[:80]}")
            return _fallback_brief(description, domain)

    normalized = _normalize_brief(brief, description, domain)
    logger.info(
        "build_landing_brief: ok — brand=%r sections=%d motif=%s header=%s footer=%s refs=%d",
        normalized["brand"]["name"],
        len(normalized["sections"]),
        normalized["motif"],
        normalized.get("header_archetype"),
        normalized.get("footer_archetype"),
        len(normalized.get("references") or []),
    )
    return normalized


# ── Grounded research call (Pro + google_search tool) ─────────────────

async def _grounded_research(
    prompt: str, timeout_s: float, *, label: str, websocket: Any = None,
) -> str:
    """One Gemini call with google_search grounding. Returns plain text.

    Falls back to ungrounded Pro generation on tool errors so research
    keeps producing useful output even if grounding is unavailable in
    the user's region or quota tier.

    Inspects ``groundingMetadata`` in the response — Gemini returns this
    only when ``google_search`` was actually invoked. A "successful" call
    that produced text WITHOUT grounding metadata means the model answered
    from training memory instead of searching the web. We surface that
    loudly so the user knows when research quality is degraded.
    """
    payload_grounded = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 6144,
            "thinkingConfig": {"thinkingBudget": 1024},
        },
        "tools": [{"google_search": {}}],
    }

    text, grounding = await _post_gemini(
        _RESEARCH_MODEL, payload_grounded, timeout_s,
        label=f"{label}_grounded",
    )
    sources = _grounding_source_count(grounding)

    # Telemetry only — never discard or retry just because metadata is missing.
    # Some Gemini regions / model versions / prompts succeed without emitting
    # groundingChunks but still produce useful output; throwing it away makes
    # the pipeline slower and worse. Log the signal, keep the text.
    if text:
        if sources > 0:
            logger.info("gemini %s_grounded: web-grounded with %d sources", label, sources)
            if websocket is not None:
                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"🌐 {label}: grounded with {sources} real web sources",
                    })
                except Exception:
                    pass
        else:
            logger.info(
                "gemini %s_grounded: returned %d chars without groundingMetadata "
                "(model may have answered without searching — keeping output)",
                label, len(text),
            )
        return text

    # Only fall back when grounded call produced NO text (HTTP error, timeout,
    # safety block). Drop the tool, try ungrounded — better than nothing.
    payload_plain = dict(payload_grounded)
    payload_plain.pop("tools", None)
    text2, _ = await _post_gemini(
        _RESEARCH_MODEL, payload_plain, timeout_s,
        label=f"{label}_plain",
    )
    return text2 or ""


def _grounding_source_count(grounding: dict | None) -> int:
    """Number of real web sources cited by the response.

    Gemini 2.5 returns ``groundingMetadata.groundingChunks`` when
    ``google_search`` was invoked. Empty / missing → grounding did not
    happen, regardless of whether the call returned 200.
    """
    if not isinstance(grounding, dict):
        return 0
    chunks = grounding.get("groundingChunks") or grounding.get("grounding_chunks") or []
    if not isinstance(chunks, list):
        return 0
    return len(chunks)


async def _structured_distill(prompt: str, timeout_s: float) -> str:
    """Final distill call — Flash + JSON output, no tools."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    text, _ = await _post_gemini(
        _BRIEF_MODEL, payload, timeout_s,
        label="brief_distill",
    )
    return text


async def _post_gemini(
    model: str, payload: dict, timeout_s: float, *, label: str,
) -> tuple[str, dict | None]:
    """POST to Gemini and extract text. Logs token usage.

    Returns ``(text, grounding_metadata)``. ``grounding_metadata`` is the
    raw ``candidates[0].groundingMetadata`` dict when present, else None.
    Routes via gemini_http (Vertex only).
    """
    from app.services.gemini_http import gemini_post

    status, data, _ = await gemini_post(
        model=model,
        payload=payload,
        timeout_s=timeout_s,
        label=f"landing_{label}",
    )
    if status != 200 or data is None:
        return "", None

    # Token billing
    try:
        from app.services.billing_meter import report_token_usage
        u = data.get("usageMetadata") or {}
        _in = int(u.get("promptTokenCount", 0) or 0)
        _out = int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0)
        if _in or _out:
            report_token_usage(None, _in, _out, source=f"gemini_landing_{label}")
    except Exception:
        pass

    try:
        from knowledge.loader import safe_gemini_text
        text = safe_gemini_text(data).strip()
    except Exception:
        text = ""
        try:
            text = (
                ((data.get("candidates") or [{}])[0]).get("content", {}).get("parts", [{}])[0].get("text", "")
            ).strip()
        except Exception:
            text = ""

    grounding: dict | None = None
    try:
        cand0 = (data.get("candidates") or [{}])[0]
        if isinstance(cand0, dict):
            gm = cand0.get("groundingMetadata") or cand0.get("grounding_metadata")
            if isinstance(gm, dict):
                grounding = gm
    except Exception:
        grounding = None

    if not text:
        logger.warning("gemini %s: empty text in response", label)
        return "", grounding

    # Persist for diagnostics on the latest run
    try:
        with open(f"/tmp/landing_{label}.txt", "w") as fh:
            fh.write(text)
    except Exception:
        pass

    logger.info("gemini %s: %d chars", label, len(text))
    return text, grounding


# ── Normalization & fallback (defensive defaults) ──────────────────────

_HEADER_ARCHETYPES = ["transparent-pill", "solid-bar", "centered-logo", "side-rail", "mega-menu"]
_FOOTER_ARCHETYPES = ["mega-columns", "minimalist-row", "cta-band-footer", "centered-stack"]


# ── design_system enum normalization ─────────────────────────────────
# Gemini regularly emits descriptive phrases ("matte with natural
# textures", "alternating spacious and dense layouts") instead of the
# single-word enum the schema asks for. Without normalization those
# strings flow into Claude's prompt verbatim and into _build_design_tokens
# which then falls back to defaults — losing whatever signal Gemini meant
# to convey. We coerce here in three passes (exact → substring → synonym)
# so designs stay close to Gemini's intent while always being valid.

_DESIGN_SYSTEM_ENUMS: dict[str, list[str]] = {
    "motion":          ["subtle", "energetic", "dramatic", "organic"],
    "accent_shape":    ["squared", "rounded", "pill", "blob", "hairline"],
    "surface":         ["flat", "elevated", "bordered", "layered", "duotone"],
    "image_treatment": ["natural", "overlay", "duotone", "masked", "bordered"],
    "section_rhythm":  ["tight", "balanced", "airy"],
}

_DESIGN_SYSTEM_DEFAULTS: dict[str, str] = {
    "motion": "subtle",
    "accent_shape": "rounded",
    "surface": "elevated",
    "image_treatment": "natural",
    "section_rhythm": "balanced",
}

_DESIGN_SYNONYMS: dict[str, dict[str, str]] = {
    "surface": {
        "matte":            "flat",
        "natural texture":  "bordered",
        "natural textures": "bordered",
        "glossy":           "elevated",
        "shiny":            "elevated",
        "glass":            "layered",
        "frosted":          "layered",
        "blur":             "layered",
        "shadowed":         "elevated",
        "raised":           "elevated",
        "alternating":      "duotone",
        "two-tone":         "duotone",
    },
    "section_rhythm": {
        "spacious":    "airy",
        "alternating": "balanced",
        "dense":       "tight",
        "compact":     "tight",
        "loose":       "airy",
        "generous":    "airy",
        "rhythmic":    "balanced",
    },
    "motion": {
        "smooth":   "organic",
        "playful":  "energetic",
        "bold":     "dramatic",
        "minimal":  "subtle",
        "static":   "subtle",
        "calm":     "subtle",
        "lively":   "energetic",
        "cinematic": "dramatic",
        "fluid":    "organic",
    },
    "accent_shape": {
        "soft":     "rounded",
        "circular": "rounded",
        "round":    "rounded",
        "sharp":    "squared",
        "square":   "squared",
        "thin":     "hairline",
        "fine":     "hairline",
        "organic":  "blob",
        "amorphous": "blob",
    },
    "image_treatment": {
        "tinted":   "duotone",
        "framed":   "bordered",
        "raw":      "natural",
        "vignette": "overlay",
        "darkened": "overlay",
        "clipped":  "masked",
    },
}


def _normalize_design_system(ds: dict | None) -> dict:
    """Coerce design_system fields to valid single-word enums.

    Order: exact enum match → substring scan → synonym table → default.
    Always returns all 5 fields populated with a valid enum value.
    """
    ds = ds or {}
    out: dict[str, str] = {}
    for field, allowed in _DESIGN_SYSTEM_ENUMS.items():
        raw = (ds.get(field) or "").strip().lower()
        if raw in allowed:
            out[field] = raw
            continue
        # Substring scan — pick the FIRST allowed enum that appears as a
        # word in the raw string. This handles "alternating spacious" by
        # picking up the "spacious" hint via synonyms (substring catches
        # exact enum words like "elevated" inside longer phrases).
        hit = next((a for a in allowed if a in raw), "")
        if hit:
            out[field] = hit
            continue
        # Synonym table — match longest key first so "natural texture"
        # beats "natural" / "texture".
        syn = _DESIGN_SYNONYMS.get(field, {})
        matched = ""
        for key in sorted(syn, key=len, reverse=True):
            if key in raw:
                matched = syn[key]
                break
        out[field] = matched or _DESIGN_SYSTEM_DEFAULTS[field]
    return out


# ── Design-system → concrete Tailwind class strings ──────────────────
# Why this exists: the section codegen prompt used to describe each
# design_system field as guidance text ("accent_shape: pill → rounded-full
# on buttons + rounded-2xl on cards"). Claude interpreted that loosely and
# the same brief produced different visual results across runs. By
# pre-computing the exact class strings here and injecting them as
# literals, Claude becomes a layout assembler — not a stylist.

_ACCENT_SHAPE: dict[str, dict[str, str]] = {
    # No accent_shape may produce `rounded-none` — that ships sharp 90° corners
    # which read as broken UI on every modern motif (Rule 12 in the codegen
    # prompt). Squared/hairline still feel architectural; we just keep a 4–8px
    # radius so buttons/cards have a defined edge.
    "squared":  {"card": "rounded-lg",    "button": "rounded-md",   "image": "rounded-md"},
    "rounded":  {"card": "rounded-xl",    "button": "rounded-md",   "image": "rounded-xl"},
    "pill":     {"card": "rounded-2xl",   "button": "rounded-full", "image": "rounded-2xl"},
    "blob":     {"card": "rounded-3xl",   "button": "rounded-full",
                 "image": "rounded-[40%_60%_70%_30%/40%_50%_60%_50%]"},
    "hairline": {"card": "rounded-lg",    "button": "rounded-md",   "image": "rounded-md"},
}

_SURFACE: dict[str, str] = {
    "flat":     "bg-card",
    "elevated": "bg-card shadow-md hover:shadow-xl",
    "bordered": "bg-card border-2 border-border",
    "layered":  "bg-card/80 backdrop-blur-sm border border-border",
    "duotone":  "bg-card",  # alternates per index — handled in JSX
}

_MOTION: dict[str, dict[str, str]] = {
    "subtle":    {"transition": "transition-all duration-300",
                  "hover":      "hover:-translate-y-0.5 hover:shadow-md"},
    "energetic": {"transition": "transition-all duration-300",
                  "hover":      "hover:-translate-y-1 hover:shadow-lg"},
    "dramatic":  {"transition": "transition-all duration-500",
                  "hover":      "hover:-translate-y-2 hover:scale-[1.02] hover:shadow-2xl"},
    "organic":   {"transition": "transition-all duration-700 ease-out",
                  "hover":      "hover:-translate-y-1 hover:shadow-md"},
}

_RHYTHM: dict[str, str] = {
    "tight":    "py-12 sm:py-16",
    "balanced": "py-16 sm:py-20 lg:py-24",
    "airy":     "py-24 sm:py-32 lg:py-40",
}


# ───────────────────────── icon name normalization ────────────────────────
# Common lowercase aliases Gemini emits for things that don't match Lucide's
# PascalCase exports. Anything not in this map falls back to a snake/kebab →
# PascalCase conversion. Anything that ends up non-alphabetic is dropped.
_ICON_LOWERCASE_ALIASES: dict[str, str] = {
    # Social / brand
    "instagram": "Instagram", "twitter": "Twitter", "facebook": "Facebook",
    "linkedin": "Linkedin", "youtube": "Youtube", "github": "Github",
    "tiktok": "Music2", "discord": "MessageSquare", "pinterest": "Bookmark",
    # Contact
    "email": "Mail", "telephone": "Phone", "location": "MapPin",
    "world": "Globe", "website": "Globe",
    # Common UI
    "close": "X", "loader": "Loader2", "spinner": "Loader2",
    "external": "ExternalLink",
    # Domain
    "cart": "ShoppingCart", "bag": "ShoppingBag",
    "logout": "LogOut", "login": "LogIn",
}


def _normalize_icon_name(raw: str | None) -> str:
    """Convert any reasonable icon-name input to a Lucide-compatible PascalCase
    identifier. Returns "" if normalization can't produce a valid JS identifier
    (caller drops the icon rather than emitting a broken import).

    Examples:
      "shopping-cart" → "ShoppingCart"
      "shopping_cart" → "ShoppingCart"
      "ShoppingCart"  → "ShoppingCart"
      "instagram"     → "Instagram"        (via aliases)
      "tiktok"        → "Music2"           (alias)
      "weird name!"   → ""                 (invalid → drop)
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # Reject strings with disallowed characters — only letters, digits, and
    # the conventional separators ` _-` can produce a safe identifier.
    if re.search(r"[^A-Za-z0-9 _\-]", s):
        return ""
    # Already PascalCase + alphanumeric? Trust it.
    if re.fullmatch(r"[A-Z][A-Za-z0-9]+", s):
        return s
    lowered = s.lower().replace(" ", "_").replace("-", "_")
    # Alias lookup for single-word lowercase names.
    if lowered in _ICON_LOWERCASE_ALIASES:
        return _ICON_LOWERCASE_ALIASES[lowered]
    # Generic snake/kebab → PascalCase.
    parts = [p for p in lowered.split("_") if p and p.isalnum()]
    if not parts:
        return ""
    pascal = "".join(p[0].upper() + p[1:] for p in parts)
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]+", pascal):
        return ""
    return pascal


def _build_design_tokens(ds: dict | None) -> dict[str, str]:
    """Map the 5 design_system fields to concrete Tailwind class strings.

    Falls through to the safest default ("rounded" / "elevated" / "subtle"
    / "balanced") for any value Gemini emits that we don't recognize, so
    Claude always gets a populated literal — never an empty class string.
    """
    ds = ds or {}
    accent = (ds.get("accent_shape") or "rounded").strip().lower()
    surface = (ds.get("surface") or "elevated").strip().lower()
    motion = (ds.get("motion") or "subtle").strip().lower()
    rhythm = (ds.get("section_rhythm") or "balanced").strip().lower()
    image_treatment = (ds.get("image_treatment") or "natural").strip().lower()

    shape = _ACCENT_SHAPE.get(accent, _ACCENT_SHAPE["rounded"])
    surface_class = _SURFACE.get(surface, _SURFACE["elevated"])
    motion_pair = _MOTION.get(motion, _MOTION["subtle"])
    rhythm_class = _RHYTHM.get(rhythm, _RHYTHM["balanced"])

    # `card_class` is the full literal a card should use — surface bg/border
    # + radius — so Claude can paste it once instead of composing.
    card_class = f"{surface_class} {shape['card']}".strip()
    button_class = f"{shape['button']}"
    image_radius_class = shape["image"]

    return {
        "card_class":          card_class,
        "button_radius_class": button_class,
        "image_radius_class":  image_radius_class,
        "section_padding_class": rhythm_class,
        "transition_class":    motion_pair["transition"],
        "hover_lift_class":    motion_pair["hover"],
        # Echo the resolved tokens so prompts can show "you picked X"
        "_resolved_accent_shape":    accent,
        "_resolved_surface":         surface,
        "_resolved_motion":          motion,
        "_resolved_section_rhythm":  rhythm,
        "_resolved_image_treatment": image_treatment,
    }


def _normalize_brief(brief: Any, description: str, domain: str) -> dict:
    # Gemini occasionally returns the brief wrapped in a single-element
    # array (`[{...}]`) instead of a bare object, despite responseMimeType
    # = application/json. Unwrap so downstream code sees a dict.
    if isinstance(brief, list):
        if len(brief) == 1 and isinstance(brief[0], dict):
            brief = brief[0]
        elif all(isinstance(item, dict) for item in brief):
            logger.warning(
                "normalize_brief: Gemini returned a top-level section array — wrapping as sections"
            )
            brief = {"sections": brief}
        else:
            logger.warning(
                "normalize_brief: unsupported top-level list shape (%d items) — fallback",
                len(brief),
            )
            return _fallback_brief(description, domain)
    if isinstance(brief, dict):
        for wrapper_key in ("brief", "landing_brief", "landingBrief", "data", "result"):
            wrapped = brief.get(wrapper_key)
            if isinstance(wrapped, dict):
                logger.info("normalize_brief: unwrapped Gemini %s envelope", wrapper_key)
                brief = wrapped
                break
    else:
        logger.warning(
            "normalize_brief: expected dict, got %s — fallback",
            type(brief).__name__,
        )
        return _fallback_brief(description, domain)
    out = dict(brief)

    # Gemini sometimes returns explicit `null` for palette / brand slots; a
    # plain dict-merge would overwrite the safe defaults with None and ship
    # `--primary: None;` into globals.css, breaking every Tailwind class
    # bound to that token. Strip None values from the override side first.
    def _as_dict(value: Any) -> dict:
        return value if isinstance(value, dict) else {}

    def _as_list(value: Any) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _as_str(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, str):
            return value
        return str(value)

    def _as_string_list(value: Any) -> list[str]:
        out_values: list[str] = []
        for item in _as_list(value):
            if isinstance(item, (str, int, float)):
                text = str(item).strip()
                if text:
                    out_values.append(text)
        return out_values

    def _drop_nones(d: Any) -> dict:
        return {k: v for k, v in _as_dict(d).items() if v is not None}

    out["brand"] = {**_default_brand(description, domain), **_drop_nones(brief.get("brand"))}
    out["palette"] = {**_default_palette(), **_drop_nones(brief.get("palette"))}
    out["typography"] = {**_default_typography(), **_drop_nones(brief.get("typography"))}
    out["motif"] = _as_str(brief.get("motif"), "minimal").strip().lower() or "minimal"
    # Normalize Gemini's freeform design_system values to valid enums BEFORE
    # they reach Claude's prompt or _build_design_tokens. See
    # _normalize_design_system for the coercion logic.
    out["design_system"] = _normalize_design_system(
        {**_default_design_system(), **_drop_nones(brief.get("design_system"))}
    )
    out["domain_keywords"] = _as_string_list(brief.get("domain_keywords")) or [domain]

    # Personality
    pers = _as_dict(brief.get("personality"))
    out["personality"] = {
        "tone": _as_str(pers.get("tone"), "confident").strip() or "confident",
        "vibe_keywords": _as_string_list(pers.get("vibe_keywords"))[:5] or ["modern", "clear", "trustworthy"],
        "energy": _as_str(pers.get("energy"), "medium").strip().lower() or "medium",
    }

    # Header / footer archetypes
    h_arch = _as_str(brief.get("header_archetype")).strip().lower()
    out["header_archetype"] = h_arch if h_arch in _HEADER_ARCHETYPES else random.choice(_HEADER_ARCHETYPES)
    f_arch = _as_str(brief.get("footer_archetype")).strip().lower()
    out["footer_archetype"] = f_arch if f_arch in _FOOTER_ARCHETYPES else random.choice(_FOOTER_ARCHETYPES)

    # References
    out["references"] = _as_list(brief.get("references"))[:6]

    ctas = _as_dict(brief.get("ctas"))
    out["ctas"] = {
        "primary": ctas.get("primary") or {"label": "Get started", "href": "#contact"},
        "secondary": ctas.get("secondary") or {"label": "Learn more", "href": "#features"},
    }

    sections = [s for s in _as_list(brief.get("sections")) if isinstance(s, dict)]
    if not sections:
        sections = _default_sections()

    seen_ids: set[str] = set()
    for s in sections:
        sid = _as_str(s.get("id") or s.get("type"), "section").strip().lower().replace(" ", "-") or "section"
        base = sid
        n = 2
        while sid in seen_ids:
            sid = f"{base}-{n}"
            n += 1
        seen_ids.add(sid)
        s["id"] = sid
        s.setdefault("type", base)
        s.setdefault("role", "")
        s.setdefault("headline", "")
        s.setdefault("layout_hint", "centered-stack")
        s["items"] = _as_list(s.get("items"))
        s["image_queries"] = _as_list(s.get("image_queries"))
        s.setdefault("interactivity", "")

        # Normalize each item.icon to a PascalCase Lucide identifier. Gemini
        # frequently emits kebab-case ("shopping-cart") or snake_case
        # ("shopping_cart") because that's how humans say icon names; both
        # break `import {{ shopping-cart }} from "lucide-react"` with an
        # invalid-identifier syntax error before the build even starts.
        items = s.get("items") or []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_icon_name(item.get("icon"))
                if normalized:
                    item["icon"] = normalized
                elif "icon" in item:
                    # Drop unrecognized icons — codegen falls back to the
                    # icon-less variant rather than emitting a broken import.
                    item.pop("icon", None)

    if not any((s.get("type") or "").lower() == "hero" for s in sections):
        sections.insert(0, _default_hero(out["brand"]["name"], out["brand"]["tagline"]))

    # Validate per-type archetype against the allowed library.
    _ARCH_LIBRARY: dict[str, list[str]] = {
        "hero":         ["full-bleed-overlay", "oversized-watermark", "asymmetric-split",
                         "type-wrapping-product", "video-mask", "card-stack"],
        "menu":         ["two-column-dotted", "photo-card-grid", "categorized-rows"],
        "gallery":      ["asymmetric-12col", "marquee-scroll", "bento-mosaic"],
        "testimonials": ["glass-cards-bg", "marquee-row", "big-quote-portrait"],
        "features":     ["icon-grid-3", "numbered-stepper", "split-image-bullets"],
        "value_prop":   ["icon-grid-3", "numbered-stepper", "split-image-bullets"],
        "benefits":     ["icon-grid-3", "numbered-stepper", "split-image-bullets"],
        "how_it_works": ["numbered-stepper", "icon-grid-3", "split-image-bullets"],
        "process":      ["numbered-stepper", "icon-grid-3", "split-image-bullets"],
    }
    for s in sections:
        stype = _as_str(s.get("type")).lower()
        if stype not in _ARCH_LIBRARY:
            continue
        valid = _ARCH_LIBRARY[stype]
        arch = _as_str(s.get("archetype")).strip().lower()
        if arch not in valid:
            if arch:
                logger.info("normalize_brief: unknown %s archetype %r — random fallback", stype, arch)
            s["archetype"] = random.choice(valid)

    if not any((s.get("type") or "").lower() == "footer" for s in sections):
        sections.append(_default_footer(out["brand"]["name"]))

    # At most ONE form-style section.
    _FORMS = {"contact", "contact_form", "reservation", "booking_form", "newsletter"}
    seen_form = False
    deduped: list[dict] = []
    for s in sections:
        t = _as_str(s.get("type")).lower()
        if t in _FORMS:
            if seen_form:
                logger.info("normalize_brief: dropping duplicate form section %r", s.get("id"))
                continue
            seen_form = True
        deduped.append(s)
    sections = deduped

    out["sections"] = sections
    out["design_tokens"] = _build_design_tokens(out["design_system"])
    return out


def _fallback_brief(description: str, domain: str) -> dict:
    brand = _default_brand(description, domain)
    ds = _default_design_system()
    return {
        "brand": brand,
        "palette": _default_palette(),
        "typography": _default_typography(),
        "motif": "minimal",
        "design_system": ds,
        "design_tokens": _build_design_tokens(ds),
        "personality": {"tone": "confident", "vibe_keywords": ["modern", "clear"], "energy": "medium"},
        "header_archetype": "solid-bar",
        "footer_archetype": "minimalist-row",
        "references": [],
        "sections": _default_sections(brand_name=brand["name"], tagline=brand["tagline"]),
        "ctas": {
            "primary": {"label": "Get started", "href": "#contact"},
            "secondary": {"label": "Learn more", "href": "#features"},
        },
        "domain_keywords": [domain] if domain and domain != "general" else ["product", "modern", "clean"],
    }


def _default_brand(description: str, domain: str) -> dict:
    first = (description or "").strip().split("\n")[0][:60] or "Untitled"
    return {
        "name": first.split()[0].title() if first else "Untitled",
        "tagline": "Built for what's next",
        "description": (description or "")[:240],
        "domain": domain or "general",
    }


def _default_palette() -> dict:
    return {
        "primary":    "220 90% 56%",
        "secondary":  "220 14% 96%",
        "accent":     "262 83% 58%",
        "background": "0 0% 100%",
        "foreground": "222 47% 11%",
        "muted":      "210 40% 96%",
        "border":     "214 32% 91%",
        "card":       "0 0% 100%",
    }


def _default_typography() -> dict:
    return {"heading_font": "Inter", "body_font": "Inter", "scale": "balanced"}


def _default_design_system() -> dict:
    return {
        "motion": "subtle",
        "accent_shape": "rounded",
        "surface": "elevated",
        "image_treatment": "natural",
        "section_rhythm": "balanced",
    }


def _default_hero(brand: str, tagline: str) -> dict:
    return {
        "id": "hero",
        "type": "hero",
        "role": "convert visitor",
        "archetype": "full-bleed-overlay",
        "interactivity": "primary CTA smooth-scrolls to first form / contact section",
        "headline": tagline or f"Welcome to {brand}",
        "subheadline": f"{brand} — built to solve real problems for real people.",
        "layout_hint": "centered-stack",
        "items": [],
        "image_queries": ["modern hero scene"],
        "cta": {"label": "Get started", "href": "#contact"},
    }


def _default_footer(brand: str) -> dict:
    return {
        "id": "footer",
        "type": "footer",
        "role": "site-wide nav",
        "headline": brand,
        "subheadline": "",
        "layout_hint": "footer-columns",
        "items": [],
        "image_queries": [],
    }


def _try_salvage_truncated_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    s = s.strip()
    if not s.startswith("{"):
        return None

    def _walk(text: str) -> tuple[list[str], int]:
        in_str = False
        str_start = -1
        last_open_str = -1
        esc = False
        stack: list[str] = []
        for i, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if in_str:
                if ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                str_start = i
                last_open_str = i
                continue
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if stack:
                    stack.pop()
        return stack, (str_start if in_str else -1)

    candidate = s
    stack0, open_str = _walk(candidate)
    if open_str >= 0:
        candidate = candidate[:open_str].rstrip().rstrip(":").rstrip()
        cut = max(candidate.rfind(","), candidate.rfind("{"), candidate.rfind("["))
        if cut > 0:
            candidate = candidate[:cut]

    for _ in range(40):
        trimmed = candidate.rstrip().rstrip(",")
        if not trimmed:
            return None
        stack, open_str = _walk(trimmed)
        if open_str < 0:
            attempt = trimmed + "".join(reversed(stack))
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, dict) and parsed.get("brand"):
                    return parsed
            except Exception:
                pass
        cut = max(trimmed.rfind(","), trimmed.rfind("{"), trimmed.rfind("["))
        if cut <= 0:
            return None
        candidate = trimmed[:cut]
    return None


def _default_sections(brand_name: str = "Untitled", tagline: str = "Built for what's next") -> list[dict]:
    return [
        _default_hero(brand_name, tagline),
        {
            "id": "features",
            "type": "features",
            "role": "explain value",
            "headline": "What you get",
            "subheadline": "Three things we do better than anyone else.",
            "layout_hint": "grid-3",
            "interactivity": "cards lift on hover with smooth shadow transition",
            "items": [
                {"title": "Fast", "description": "Built for speed.", "icon": "Zap"},
                {"title": "Reliable", "description": "Always on.", "icon": "ShieldCheck"},
                {"title": "Simple", "description": "Clear by design.", "icon": "Sparkles"},
            ],
            "image_queries": [],
        },
        {
            "id": "cta",
            "type": "cta",
            "role": "convert",
            "headline": "Ready to start?",
            "subheadline": "Get in touch and we'll take it from there.",
            "layout_hint": "centered-stack",
            "interactivity": "CTA smooth-scrolls to #contact",
            "items": [],
            "cta": {"label": "Contact us", "href": "#contact"},
            "image_queries": [],
        },
        _default_footer(brand_name),
    ]
