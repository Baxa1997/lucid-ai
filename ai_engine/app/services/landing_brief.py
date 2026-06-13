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
  3. DISTILL              (gemini-3.5-flash, structured JSON output)
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
        "page_features": {
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

1. Search for 8-12 reference websites that match the prompt domain. Cast a WIDE net — don't stop at 4 sites. Include a mix:
   • 4-6 from the user's exact domain (e.g. "Brooklyn restaurant" → award-winning Brooklyn restaurants)
   • 2-3 from adjacent premium examples (e.g. Michelin-starred restaurants worldwide, not just Brooklyn)
   • 2-3 cross-pollinated from sibling domains that share conversion mechanics (e.g. for "tour operator" → Airbnb Experiences, Viator, GetYourGuide for booking flows; for "boutique hotel" → Aman, Soho House, Six Senses for hospitality patterns).
   A narrow set of 4 sites in the same sub-niche misses the conversion patterns that appear once you broaden the lens.

2. For each reference, list:
   • URL + name
   • SECTION ORDER as it appears on their HOMEPAGE (e.g. hero → philosophy → menu → reservations → gallery → press → footer)
   • For multi-page sites: the key OTHER pages (about, menu, reservations, contact) and 1-line of what each page contains
   • NOTABLE INTERACTIVE FEATURES (e.g. "menu items open a modal with photo + ingredients", "sticky reservation widget bottom-right", "lightbox gallery with keyboard nav", "animated stat counters on scroll", "filter pills for menu categories")
   • Why this site is a good reference (1 sentence)

3. CONCLUSION: synthesize a recommended section blueprint for THIS prompt by picking from the patterns common across references.
   • Pick HOW MANY sections this landing needs — DO NOT cap at 7. Match what the references
     actually do: a tour-operator or hotel landing often runs 9-12 (hero → trust bar →
     curated grid → mid-page CTA → social proof → FAQ → lead form → sticky CTA), while a
     minimalist agency portfolio can ship in 5. The minimum is 5; there is no maximum
     beyond what the references support. List EVERY section in order, with a 1-line "why
     this is here" and the interactive behavior it must have.
   • Flag CONVERSION-COMPLETENESS gaps. If your reference set covers any of these
     elements, the conclusion MUST include them:
       - Sticky CTA bar / floating chat (page-level, follows scroll)
       - Hero search/filter widget (tour type, dates, destination, role, plan…)
       - Inline trust ticker ("12 yrs · 60+ destinations · 2,400+ travelers")
       - Mid-page CTA banner (re-engagement halfway down)
       - Full lead/quote form with name + email + intent fields (NOT just a newsletter input)
       - FAQ accordion
     Page-level chrome (sticky CTA, floating chat) goes under a NEW "===PAGE_FEATURES==="
     block, not in the section list. Section-level items (trust ticker, mid CTA, lead
     form, FAQ) go in the section list as their own section types.

OUTPUT FORMAT — plain markdown, no JSON yet. Use these section headers:

===REFERENCES===
1. <name> — <url>
   Sections: hero → ... → footer
   Pages: home, /menu (...), /reservations (...), /about (...)
   Page chrome: <sticky_cta | floating_chat | exit_intent | back_to_top | whatsapp_button | none>
   Features: ...
   Why: ...
2. ...

===CONCLUSION===
Recommended sections (hero first, no footer — pick from references; choose your own count, no upper cap):
1. hero — <archetype hint based on what references do> — <interactivity, e.g. "search filter for tour type + dates + travelers">
2. <type> — <archetype hint> — <interactivity>
... however many sections the references support, minimum 5 ...

===PAGE_FEATURES===
- <sticky_cta | floating_chat | exit_intent | back_to_top | whatsapp_button>: <why this matters for this domain>
(omit the section entirely if no reference uses any page-level chrome)

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

1. Search for 8-12 reference sites in this domain (use awwwards / siteinspire / google for "<domain> website 2025" or specific brand names).
   Include a mix: 4-6 best-in-class in the exact niche, 2-3 from one tier up (luxury / award-winners worldwide), and 2-3 from adjacent industries with sibling visual languages (e.g. for "boutique hotel" → Aesop, Apartamento, Ace Hotel for editorial premium feel).
   Wide-lens design DNA produces more interesting palettes than narrow same-niche scraping.

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

===REFERENCES===  (must contain ≥8 distinct REAL URLs from search results)
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


_CONVERSION_RESEARCH_PROMPT = """You are a senior CRO (conversion rate optimization) researcher. USE GOOGLE SEARCH — do NOT answer from training memory.

PROMPT: "{description}"
DOMAIN: {domain}

YOUR JOB — identify which conversion-optimized landing-page patterns this project should adopt. Focus on REAL examples that BOOK / BUY / SIGN UP / GET A QUOTE — not editorial / portfolio sites.

1. Search for 6-10 high-converting landing pages relevant to this domain. Run AT LEAST these searches:
     a) "best [domain] landing pages 2025"
     b) "[domain] high converting examples"
     c) the top 2-3 brand names in the user's space (e.g. for tours → Viator, GetYourGuide, Intrepid Travel; for SaaS → Linear, Notion, Stripe; for hotels → Aman, Six Senses; for restaurants → Carbone, Atomix; for dental → Tend, Smile Direct Club). USE actual brand names from search results — never invent.
   Bias toward sites whose PRIMARY CTA is a paid action.

2. For each reference, produce a COVERAGE MATRIX — which of these conversion patterns the site uses (yes/no per element). Be honest; don't mark "yes" unless the element is visible on the homepage.

   • Sticky CTA bar      — does a "Book / Buy / Get Quote / Try Free" CTA follow the user as they scroll (top, bottom, or floating)?
   • Hero search/filter  — does the hero contain a search widget, date picker, plan toggle, or category filter (e.g. dates+travelers, monthly/yearly, role selector)?
   • Inline trust bar    — slim strip near the top with "X yrs · Y customers · Z% rating" or press-logo row?
   • Mid-page CTA banner — a re-engagement banner roughly halfway down the page (NOT the hero CTA, NOT the final cta)?
   • Full lead/quote form — a multi-field form (name, email, intent, dates/role/etc.), NOT just newsletter email?
   • FAQ accordion       — visible FAQ block addressing objections?
   • Social proof block  — testimonials with names/photos, ratings, or media logos?
   • Floating chat       — chat bubble bottom-right, or WhatsApp button?

3. CONCLUSION — give a "minimum conversion set" for THIS project: the elements that 60%+ of the references use AND are necessary for the user's stated CTA. Mark each element as REQUIRED / RECOMMENDED / OPTIONAL based on reference frequency. Call out anything the references universally avoid (so we don't force it in).

OUTPUT FORMAT — plain markdown. No JSON.

===REFERENCES_COVERAGE===
| Site | sticky_cta | hero_filter | trust_bar | mid_cta | lead_form | faq | social_proof | floating_chat |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Viator (viator.com) | yes | yes | yes | yes | no | yes | yes | yes |
| ... ≥6 rows ...

===CONCLUSION===
Required (≥60% of references):  <comma-separated keys>
Recommended (30-60%):           <comma-separated keys>
Optional (<30%):                <comma-separated keys>

Notes: <1-2 sentences on anything unusual — e.g. "luxury hotel references almost never use sticky CTAs because the brand demands restraint; recommended only if scroll depth is long.">
"""


_DISTILL_PROMPT = """You are briefing an engineer to build ONE landing page. You have THREE research dumps from real reference sites. Your job: distill them into a SINGLE structured Brief for this project.

PROJECT: "{description}"
DOMAIN: {domain}

===STRUCTURE_RESEARCH===
{structure_research}

===DESIGN_DNA_RESEARCH===
{design_dna_research}

===CONVERSION_RESEARCH===
{conversion_research}

NOW produce the Brief as a JSON object matching the response schema EXACTLY. No prose outside the JSON.

REQUIREMENTS — every string field has a character limit, OBEY THEM.

brand: name (real, ≤30 chars); tagline (≤60 chars); description (≤140 chars); domain (≤30 chars).
  business_info (encouraged for local businesses): address ≤80, phone ≤24, email ≤60, hours ≤200.
    hours format: when listing MULTIPLE venues/schedules, separate entries with " · "
    (e.g. "Front Desk: 24/7 · Lounge: 7am–11pm · Pool: sunrise–sunset") — renderers split on
    '·' to put each on its own line. NEVER run schedules together without a separator.
  social (0-5 entries): each {{label: "instagram"|"facebook"|"twitter"|"linkedin"|"youtube", href: URL}}.

  ── GEOGRAPHY LOCK (single source of truth for ALL location text) ──
  Pick ONE city + ONE neighborhood + ONE country before you write ANY copy.
  Lock `business_info.address` to that location (e.g. "1245 W 6th St, Clarksville, Austin, TX 78703"),
  and EVERY downstream string — testimonials, trust bar, catering blurbs, story copy, locations
  section, footer — MUST stay inside that geography. Concretely:
    • Trust / press: cite outlets, neighborhoods, awards consistent with the city you chose
      (Austin TX → "Texas Monthly", "Eater Austin", "South Congress", "East Side"; NOT
      "Brooklyn", "Carroll Gardens", "Prospect Heights").
    • Testimonials role labels mention the same metro ("Austin foodie", "South Lamar resident"),
      NEVER another metro's neighborhoods.
    • Phone area code matches the city (Austin → 512, Brooklyn → 718, LA → 213/323, Chicago → 312).
    • Catering / events copy references local venues / events in the chosen city only.
  Failure mode to avoid: footer says "Clarksville, Austin TX 78703" but the catering section
  mentions "a Carroll Gardens block party" — that's TWO cities in one site. Pick ONE and stay
  there across every section. If you're unsure of the city, default to the one named in the
  prompt; if the prompt names none, pick a single US city and stick with it for the entire brief.

palette: 8 HSL strings ("220 90% 56%" — no hsl(), no commas) — USE the palette from DESIGN_DNA_RESEARCH conclusion. Keys: primary, secondary, accent, background, foreground, muted, border, card.
  ── PALETTE DISCIPLINE (overrides DESIGN_DNA when research is weak / generic) ──
  • `background` MUST be a warm/cool brand-tuned NEUTRAL — never pure white. Valid HSL ranges:
      warm cream    "30-45 25-45% 94-97%"   (food, hospitality, boutique, wellness)
      cool sand     "30-50 10-22% 93-96%"   (luxury, fine dining, fashion)
      cool gray     "210-220 15-25% 94-97%" (architecture, agency, premium product)
      off-white     "0 0% 96-98%"           (only for plain B2B SaaS; default to one of the above)
    Pure `0 0% 100%` is FORBIDDEN — it reads as un-styled and breaks the editorial mood.
  • `foreground` (dark text + dark sections) MUST be a brand-tuned charcoal/navy — never pure black.
    Valid HSL ranges: "210-230 20-40% 8-18%" (navy charcoal) or "30-40 10-20% 10-18%" (warm charcoal).
    Pure `0 0% 0%` is FORBIDDEN.
  • `primary` (the BRAND ACCENT) MUST be industry-appropriate. Generic blue (`220 90% 56%` ± 10°)
    is FORBIDDEN unless the domain is plainly B2B SaaS / dev tools / fintech / corporate. Map by
    domain — if DESIGN_DNA_RESEARCH gave you a generic blue for a non-SaaS domain, OVERRIDE it
    using this table:
      Food / restaurant / café / bakery       → warm terracotta (15-25° 60-75% 45-55%), burnt
                                                  amber (30-40° 70-85% 45-55%), or burgundy
                                                  (350-360° 50-65% 30-40%)
      Education / kids / family               → deep teal (180-195° 50-65% 35-45%) or forest
                                                  green (140-160° 35-50% 28-38%)
      Logistics / industrial / construction   → safety lime (75-95° 70-85% 45-55%) or signal
                                                  amber (40-50° 90-100% 50-58%)
      Healthcare / wellness / spa             → sage (95-115° 20-35% 45-55%) or teal
                                                  (170-185° 35-50% 40-50%)
      Luxury / fashion / fragrance / hotel    → muted gold (35-45° 35-55% 50-60%) or deep
                                                  navy (215-230° 45-65% 22-32%)
      Tech / SaaS / dev tools / fintech       → blue is allowed, but bias toward an UNUSUAL
                                                  hue (electric violet 260-280°, mint
                                                  165-180°, sunset orange 18-28°) when the
                                                  brand is consumer-facing.
      Agency / portfolio / studio             → black-on-cream with ONE saturated accent
                                                  (red, electric blue, lime — picked from
                                                  references).
    Choose ONE saturated brand accent — do NOT make `secondary` and `accent` BOTH saturated
    (that produces the rainbow look references avoid). `secondary` is usually a darker
    desaturated relative of the foreground; `accent` is a single companion tint to `primary`
    (e.g. for terracotta primary, a soft cream-amber accent).

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

sections: ARRAY of OBJECT items, FIRST type "hero", in order. The COUNT and SHAPE
  are DYNAMIC and category-driven (see PAGE SHAPE BY CATEGORY below). Do NOT pad
  with filler. Pick types from the STRUCTURE_RESEARCH conclusion, but FIRST narrow
  to the category shape — generic 10-section funnels are a failure, not a default.
  NO footer in this array (added downstream).
  Allowed types: features, how_it_works, pricing, testimonials, faq, stats, gallery, menu,
  integrations, comparison, team, contact, contact_form, reservation, booking_form,
  newsletter, cta, mid_cta_banner, lead_form, quote_form, trust_bar, process, benefits,
  value_prop, locations, story, philosophy, press, hours.

  ── PAGE SHAPE BY CATEGORY (single most important brief decision) ──
  Map the brand to ONE shape archetype below using DOMAIN_RESEARCH + brand category.
  Each shape lists the REQUIRED section types and which to AVOID — but the
  COUNT is driven by STRUCTURE_RESEARCH references, not by the shape. Real
  conversion-optimized landings (hospitality, SaaS, education) commonly ship
  9-12 sections and many run longer — there is NO upper cap. Lean LONGER
  whenever references support it; only go shorter for pure boutique editorial
  brands that explicitly want a magazine-spread feel.

    A. BOUTIQUE EDITORIAL
       Fine dining, boutique hotel, luxury fashion, cultural institution, art
       gallery, jeweler, bespoke service, ceremony venue.
       Required: hero (editorial) → story / philosophy → ONE flagship section
       (menu | gallery | collection | exhibitions) → press OR testimonials →
       contact / hours.
       Optional add-ons (use when references support density): chef / team,
       awards strip, private events, private dining, mailing list.
       SKIP: features grid, how_it_works, stats band, pricing tiers, faq.
       The page feels like a magazine spread, not a SaaS funnel.

    B. HOSPITALITY DENSE
       Restaurant chains, casual dining, café, mid-tier hotel, day spa,
       wellness studio, salon, tourism operator, event venue.
       Required: hero → trust_bar (3-4 chips) → flagship offering (menu | rooms |
       services | packages) → gallery → story / about → testimonials / press →
       reservation / contact → hours.
       Optional add-ons: dessert / specials, locations / map, private events,
       awards, newsletter, blog teaser, sticky_cta in page_features.
       page_features SHOULD include sticky_cta for the primary booking CTA.

    C. SAAS / DEV TOOL FUNNEL
       B2B SaaS, dev tools, fintech apps, productivity platforms, AI agents,
       analytics dashboards.
       Required: hero → trust_bar (logos OR stat strip) → features (3-6 capabilities)
       → how_it_works (numbered steps) → integrations OR comparison → pricing
       (REQUIRED if paid) → testimonials → faq → final cta.
       Optional add-ons: case_studies, security / compliance, changelog, blog,
       team, careers teaser, code_sample.
       SKIP: menu, gallery, hours, locations.

    D. EDUCATION / COURSES
       Schools, language centers, bootcamps, training programs, certification
       providers, online courses.
       Required: hero → trust_bar (accreditations / outcomes) → courses / programs
       → method / approach → format / schedule → testimonials OR outcomes →
       faq → enroll / contact_form.
       Optional add-ons: campus / facilities gallery, instructors / faculty,
       student outcomes, partners / employers, stats band.
       SKIP: pricing tiers (unless explicit pricing), integrations, comparison.
       NOTE: avoid duplicating "assessment" + "method" + "format" + "approach" as
       four separate sections — collapse to ≤2 pedagogy sections.

    E. AGENCY / PORTFOLIO
       Design studios, dev agencies, marketing shops, photographers, individual
       creatives, consultancies, architecture practices.
       Required: hero → work / portfolio → process → story / about →
       press OR clients (logo strip) → contact.
       Optional add-ons: services, team, awards, case_studies, capabilities,
       press_quotes.
       SKIP: stats band, pricing, faq, features grid, how_it_works.

    F. E-COMMERCE / D2C
       Brands selling products, D2C retail, marketplaces, single-product launches.
       Required: hero → featured products / collection → categories OR
       press → story / craftsmanship → testimonials → newsletter / community →
       contact (optional).
       Optional add-ons: gift_guides, sustainability, materials / ingredients,
       press / awards, lookbook, stockists, FAQ.
       SKIP: how_it_works (unless service), pricing tiers, integrations.

    G. SERVICES / LOCAL / LOGISTICS
       Local services (legal, accounting, plumbing, dental), logistics & freight,
       cleaning, contractors, B2B services, healthcare.
       Required: hero → services → coverage OR locations → process /
       how_it_works → testimonials OR trust_bar → contact_form / quote_form.
       Optional add-ons: team, credentials / licenses, faq, gallery (before /
       after), case_studies, press, hours, sticky_cta.
       SKIP: gallery (unless before/after), menu, integrations, pricing tiers.

    H. PUBLIC / NONPROFIT / GOVT
       Government services, public institutions, NGOs, mission organizations,
       community programs.
       Required: hero → mission / purpose → programs / initiatives → impact / stats
       → press OR partners → contact / get involved.
       Optional add-ons: events, news, leadership / board, annual_report,
       volunteer, donate, newsletter.
       SKIP: pricing, faq (unless lengthy), gallery (use stats + initiatives).

  HARD CONSTRAINTS regardless of shape:
   • Section count is driven by STRUCTURE_RESEARCH references — if the
     references show 9-12 sections (very common for hospitality / SaaS /
     education / e-commerce), MATCH that density; if they show 14+, match
     THAT. NO upper cap exists. Do NOT truncate to a small number to feel
     "tight". Optional add-ons listed above are the right pool to pull
     from when extending past the REQUIRED list.
   • NEVER pad with `mid_cta_banner` + `cta` + `newsletter` + `contact_form`
     all together — pick ONE final-conversion section.
   • NEVER include both `features` and `value_prop` and `benefits` — pick ONE.
   • NEVER include both `how_it_works` and `process` — pick ONE.
   • If the brand falls between two shapes (e.g. "education center with strong
     hospitality feel"), choose the shape whose REQUIRED sections best match
     the brand's primary conversion action.

  ── CONVERSION-COMPLETENESS CHECKLIST ──
  Read CONVERSION_RESEARCH ===CONCLUSION===. Anything marked REQUIRED there MUST appear in the
  brief (either as a section or in page_features), regardless of whether STRUCTURE_RESEARCH
  mentions it. RECOMMENDED elements should be included unless they'd genuinely hurt the brand
  (e.g. luxury hospitality dropping sticky CTAs to preserve restraint — only justified if
  CONVERSION_RESEARCH notes call that out). OPTIONAL elements are model's choice.

  Apply the section-type mapping below to any required/recommended element pulled from
  CONVERSION_RESEARCH (you decide whether it's a section or a page_feature):
  For any landing whose primary intent is a paid conversion (book, buy, schedule, quote, signup),
  the brief is INCOMPLETE without coverage of these blocks. Pull them in as their natural
  section type — do NOT collapse them into a generic `cta` if the references treat them as
  distinct sections:
    • Hero CTA           → on the hero section (always).
    • Hero search/filter → hero.interactivity must say "filter widget …" or "search bar …"
                           when domain is bookable (tours, hotels, restaurants, rentals,
                           events, services). Hero archetype "asymmetric-split" or
                           "card-stack" plays well with this.
    • Inline trust bar   → section type `trust_bar` with items like
                           {{value:"12 yrs", label:"in business"}}, {{value:"60+", label:"destinations"}}.
                           Place RIGHT below hero. Different from full `stats` section (which is
                           a full-width band — trust_bar is a slim inline strip).
    • Mid-page CTA       → section type `mid_cta_banner` placed roughly halfway down the page
                           (NOT the same as the hero CTA, NOT the same as the final cta).
                           Tied to the same primary action with a re-engagement angle.
    • Full lead form     → section type `lead_form` or `quote_form` with items like
                           {{title:"Full Name"}}, {{title:"Email"}}, {{title:"Trip Type"}},
                           {{title:"Travel Dates"}}, {{title:"Group Size"}}, {{title:"Message"}}.
                           Use INSTEAD OF `newsletter` when the primary CTA is "get a quote",
                           "request a tour", "book a consultation". `newsletter` is for
                           audience-building only (no commerce intent).
    • FAQ accordion      → section type `faq`. Required for any landing whose primary CTA
                           involves a paid action (people object before they convert).
    • Social proof       → `testimonials` or `stats` (full).
  Restaurant/food MUST include `menu` + `gallery`.
  At most ONE pure-audience-build form (`newsletter`) — and only if there's already a
  `lead_form` or `quote_form` doing the conversion work. Otherwise pick `lead_form`/`quote_form`
  alone.

page_features: ARRAY of strings (0-4 items) — page-level chrome that lives outside the
  flow of sections. Pull from STRUCTURE_RESEARCH ===PAGE_FEATURES=== block. Allowed values:
  sticky_cta, floating_chat, whatsapp_button, exit_intent, back_to_top.
  Include `sticky_cta` for ANY landing whose primary CTA is paid conversion (book / buy /
  schedule / quote / signup) — references confirm it; this is the single largest conversion
  uplift element missing from generic landings.

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

  ── ATMOSPHERE & VOICE (the difference between a brand page and a label sheet) ──
  • NARRATIVE `body` PROSE: story / experience / about / philosophy / neighborhood /
    amenities sections MUST fill `body` with 1-2 sentences of SENSORY, domain-specific
    prose — what a guest sees, hears, tastes, or feels ("Mornings start with cortados in
    the courtyard; by dusk the pool glows under string lights and vinyl hums from the
    lounge."). A page where every section has `body: ""` renders as headline-label-card
    wallpaper with no voice — the #1 "feels generic" complaint. Functional sections
    (faq, stats, forms, trust strips) keep `body` empty.
  • PHYSICAL-PLACE / EXPERIENCE BRANDS (hotel, restaurant, venue, spa, studio, campus,
    tour): the page MUST include ≥2 image-led ATMOSPHERE sections beyond the hero —
    story/spaces/amenities/dining/experience/gallery/neighborhood with image_queries
    filled for each. Booking widgets, trust strips, and FAQ sell the transaction;
    atmosphere sections sell the PLACE. A hotel page with no interior/amenity imagery
    between the hero and the footer is a travel-agency form, not a hotel.

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

  ── HEADLINE VOICE (every section.headline + the hero h1) ──
  • SPECIFIC > generic. The headline must name a CONCRETE thing about the business — a
    number, a place, a process, a sensory detail, a hard tradeoff. Editorial concrete copy
    is the single largest "doesn't look AI-generated" lever.
      ✗ "Welcome to Our Restaurant"            ✓ "Three counters. One kitchen."
      ✗ "Best Service in Town"                  ✓ "Made slow, served warm."
      ✗ "Transform Your Logistics Today"        ✓ "Freight that moves on your schedule."
      ✗ "Award-Winning Italian Dining"          ✓ "Where Naples comes to your table."
      ✗ "Premium Quality Coffee"                ✓ "Roasted Tuesday. On your counter Wednesday."
  • Use an EM-DASH (—) for a dramatic pause when the headline has two beats:
      ✓ "Built by drivers — for drivers."
      ✓ "Eleven seats. One menu. No phones at the table."
    Use a real em-dash character, not " - " (hyphen-space).
  • CTA labels must name the ACTION specifically — never "Learn More" / "Click Here".
      ✗ "Learn More"                            ✓ "See the Tasting Menu"
      ✗ "Get Started"                            ✓ "Book Your Free Diagnostic"
      ✗ "Contact Us"                             ✓ "Request a Route Quote"
  • Subheadline ≤2 lines, conversational, supports the headline with a SECOND concrete
    detail (a location, a number, a name) — not a restatement of the headline.

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
  6. HERO image subject = THE VENUE / PRODUCT / EXPERIENCE, never the city.
     "austin skyline dusk" on a garden-estate hotel hero ships downtown
     towers behind copy about a secluded courtyard — a subject mismatch the
     user reads instantly as wrong. NEVER use "skyline", "downtown",
     "cityscape", "aerial city" in a hero query unless the brand itself is
     ABOUT the city (city tours, real-estate towers). The city name may
     appear only as a TRAILING modifier after the venue subject:
       BAD  → "austin skyline at dusk", "downtown austin aerial"
       GOOD → "boutique hotel courtyard pool dusk", "garden estate hotel
              exterior austin"

DESIGN_SEED: {design_seed} — if unsure between two equally-good choices, let this seed nudge you toward the less-common one.

ctas: primary {{label, href}} required.
domain_keywords: 4-6 short Unsplash search strings.

HARD CONSTRAINTS
- Plain ASCII / natural unicode — no \\u escapes.
- Sections: minimum 5, no upper cap — pick to match STRUCTURE_RESEARCH conclusion. First MUST be hero, no footer.
- Real specific copy in brand voice.
- archetype + interactivity REQUIRED on every section that takes them.
- page_features array MUST be present (empty [] is valid for portfolios/editorial landings, but required for any paid-conversion domain).
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

    # ── Stage 1+2+3: parallel grounded research ──────────────────────
    # Three calls in parallel — each focused on a different axis:
    #   • structure  → section blueprint from same-domain references
    #   • design_dna → palette, typography, motif from premium examples
    #   • conversion → coverage matrix of CRO patterns (sticky CTA,
    #                  hero filter, trust bar, mid CTA, lead form, FAQ)
    # The third call closes the gap that pure domain research leaves —
    # references in narrow niches may all skip FAQ/sticky CTAs, but the
    # broader landing-page CRO playbook still applies.
    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "🔎 Researching real reference sites + design DNA + conversion patterns in parallel...",
            })
        except Exception:
            pass

    structure_prompt   = _STRUCTURE_RESEARCH_PROMPT.format(description=description.strip(), domain=domain)
    design_prompt      = _DESIGN_DNA_RESEARCH_PROMPT.format(description=description.strip(), domain=domain)
    conversion_prompt  = _CONVERSION_RESEARCH_PROMPT.format(description=description.strip(), domain=domain)

    structure_text, design_text, conversion_text = await asyncio.gather(
        _grounded_research(structure_prompt,  timeout_s, label="structure_research",  websocket=websocket),
        _grounded_research(design_prompt,     timeout_s, label="design_dna_research", websocket=websocket),
        _grounded_research(conversion_prompt, timeout_s, label="conversion_research", websocket=websocket),
    )

    # If ALL research calls failed completely we have nothing to distill;
    # fall through to the legacy ungrounded distill so the pipeline still
    # produces a Brief. A partial failure (1-2 of 3 empty) still proceeds.
    if not structure_text and not design_text and not conversion_text:
        logger.warning("build_landing_brief: all research calls returned empty — distilling without research context")
        structure_text  = "(research unavailable)"
        design_text     = "(research unavailable)"
        conversion_text = "(research unavailable)"

    # ── Stage 4: distill into structured Brief ───────────────────────
    if websocket is not None:
        try:
            from app.services.llm_retry import emit_brief_distill_started
            await emit_brief_distill_started(websocket)
        except Exception:
            pass

    distill_prompt = _DISTILL_PROMPT.format(
        description=description.strip(),
        domain=domain,
        structure_research=(structure_text or "(none)")[:8000],
        design_dna_research=(design_text or "(none)")[:8000],
        conversion_research=(conversion_text or "(none)")[:6000],
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

    # At most ONE form-style section. lead_form / quote_form are
    # paid-conversion forms and take precedence over newsletter when
    # both are present (newsletter is audience-build only).
    _CONVERSION_FORMS = {"lead_form", "quote_form", "contact_form", "reservation", "booking_form", "contact"}
    _AUDIENCE_FORMS = {"newsletter"}
    _FORMS = _CONVERSION_FORMS | _AUDIENCE_FORMS
    has_conversion_form = any(_as_str(s.get("type")).lower() in _CONVERSION_FORMS for s in sections)
    seen_form = False
    deduped: list[dict] = []
    for s in sections:
        t = _as_str(s.get("type")).lower()
        if t in _FORMS:
            # If both a lead/quote/contact form AND a newsletter are present,
            # the newsletter is the redundant one — drop it.
            if t in _AUDIENCE_FORMS and has_conversion_form:
                logger.info("normalize_brief: dropping newsletter %r — conversion form already present", s.get("id"))
                continue
            if seen_form:
                logger.info("normalize_brief: dropping duplicate form section %r", s.get("id"))
                continue
            seen_form = True
        deduped.append(s)
    sections = deduped

    # ── FAMILY DEDUP — drop redundant feature/process duplicates only ──
    # Pages with features + value_prop + benefits stacked together read as
    # the "generic 10-section funnel" failure mode. Same for how_it_works +
    # process. Drop the second one when both appear. No hard cap on TOTAL
    # section count — that's Gemini's call based on category + references.
    _FEATURES_FAMILY = ("features", "value_prop", "benefits", "capabilities")
    _PROCESS_FAMILY = ("how_it_works", "process", "method", "approach")
    seen_family: dict[str, str] = {}  # family_key → first kept section type
    coherent: list[dict] = []
    for s in sections:
        t = _as_str(s.get("type")).lower()
        family = None
        if t in _FEATURES_FAMILY:
            family = "features"
        elif t in _PROCESS_FAMILY:
            family = "process"
        if family:
            if family in seen_family:
                logger.info(
                    "normalize_brief: dropping redundant %s-family section %r (kept %r)",
                    family, s.get("id"), seen_family[family],
                )
                continue
            seen_family[family] = t
        coherent.append(s)

    if len(coherent) != len(sections):
        logger.info(
            "normalize_brief: family dedup — %d → %d sections",
            len(sections), len(coherent),
        )
    sections = coherent

    out["sections"] = sections
    out["design_tokens"] = _build_design_tokens(out["design_system"])

    # ── Normalize page_features (page-level chrome) ────────────────
    # Pull from brief output; whitelist-filter to the allowed values.
    # Empty list is valid (portfolio / editorial landings rarely need
    # sticky CTAs). Required surface — downstream codegen reads this
    # to decide whether to mount <StickyCTA/>, <FloatingChat/>, etc.
    _ALLOWED_PAGE_FEATURES = {
        "sticky_cta", "floating_chat", "whatsapp_button", "exit_intent", "back_to_top",
    }
    raw_features = brief.get("page_features") or []
    if isinstance(raw_features, list):
        features = [
            _as_str(f).strip().lower()
            for f in raw_features
            if _as_str(f).strip().lower() in _ALLOWED_PAGE_FEATURES
        ]
        # Dedup while preserving order.
        seen: set[str] = set()
        out["page_features"] = [f for f in features if not (f in seen or seen.add(f))]
    else:
        out["page_features"] = []

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
        "page_features": [],
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
    """Fallback palette used when research is missing or partial.

    Previously this was bright SaaS blue (220 90% 56%) on pure white
    (0 0% 100%) — the worst possible fallback because it produced bland
    generic output AND quietly overrode partial-research palettes
    (the brief merges defaults-first, then brief.palette via dict-spread).

    New defaults: a warm editorial neutral with a terracotta primary and
    sage accent. Tinted background (not pure #fff), distinctive primary
    (saturation 55% > the 45% floor we enforce in research), brand-distinctive
    enough to look intentional even when research fails entirely. Still
    overridden by anything research returns.
    """
    return {
        "primary":    "15 55% 48%",   # warm terracotta
        "secondary":  "180 18% 38%",  # muted sage-teal
        "accent":     "40 75% 55%",   # warm mustard
        "background": "35 22% 96%",   # tinted warm cream (not #fff)
        "foreground": "20 28% 16%",   # warm near-black
        "muted":      "30 18% 92%",
        "border":     "28 18% 84%",
        "card":       "40 28% 98%",
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
