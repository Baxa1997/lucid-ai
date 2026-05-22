"""Per-section content schemas — single source of truth.

Both the content writer (Gemini Flash, in expand_page_brief) and the layout
writer (Claude, in page_codegen) read from these schemas. Gemini fills them,
Claude reads them. No drift, no shape invention.

Schema format
=============
Each schema is a dict where:
  • keys are field names (these become JSON keys + content.<field> references in JSX)
  • values are either:
      - a string starting with "STRING — <hint>" (a leaf field)
      - a string starting with "ARRAY[<n>]: <hint>" then a sub-schema for items
      - a nested dict (a structured object field)
      - a list with one element (an array of objects matching that element's schema)

Example consumers:
  Gemini sees: "headline": "STRING — 5-10 words bold headline"
  Gemini fills: "headline": "Simple, transparent pricing."

Adding a new section type
=========================
1. Add a key to SECTION_SCHEMAS below.
2. Define its shape with the conventions above.
3. (Optional) Add it to SECTION_TYPE_ALIASES if the brief might emit synonyms.
4. (Optional) Add an entry to SECTION_DESCRIPTIONS for richer Gemini hints.
That's it — page_codegen and expand_page_brief pick it up automatically.
"""
from __future__ import annotations

from typing import Any


# ── Per-section content shapes ───────────────────────────────────────

SECTION_SCHEMAS: dict[str, dict[str, Any]] = {

    "hero": {
        "headline": "STRING — 5-10 words, bold and direct, the page's primary message. Must use the brand's actual voice (industry vocab + regional anchors) — never generic SaaS-speak.",
        "subheadline": "STRING — 1-2 sentences supporting the headline, concrete nouns and numbers",
        "body": "STRING — optional 1-2 sentence supporting paragraph; '' if not needed",
        "primary_cta": {
            "label": "STRING — button text, 2-4 words, action verb",
            "href": "STRING — link target, e.g. /signup or #pricing",
        },
        "secondary_cta": {
            "label": "STRING — empty string if no secondary CTA",
            "href": "STRING — empty string if no secondary CTA",
        },
        "image": {
            "url": "STRING — '' (the downstream Unsplash binder fills this from image_queries[0])",
            "alt": "STRING — descriptive alt text, '' if no image",
        },
        "image_queries": "ARRAY[1]: STRING — exactly ONE Unsplash search query (3-6 words). Must be a CONCRETE SUBJECT noun rooted in this brand's actual work — e.g. 'freight truck highway sunset' (not 'logistics hero'); 'sourdough crust crumb closeup' (not 'restaurant hero'); 'boutique paris balcony wood floor' (not 'hotel hero'). NEVER 'modern <industry> hero' — that's exactly the generic pattern we are killing.",
    },

    "value_prop": {
        "headline": "STRING — section headline, 4-8 words",
        "subheadline": "STRING — 1 sentence elaboration, '' if not needed",
        "items": [
            {
                "title": "STRING — value bullet title, 2-4 words",
                "body": "STRING — 1-2 sentence CONCRETE benefit (not 'we do it well'); cite an actual differentiator",
                "icon": "STRING — lucide-react icon name, e.g. 'Zap', 'Shield', 'Star'",
            },
        ],
    },

    "features": {
        "headline": "STRING — section headline, 4-8 words",
        "subheadline": "STRING — 1 sentence elaboration",
        "items": [
            {
                "title": "STRING — feature title (2-5 words, specific capability)",
                "body": "STRING — 1-2 sentence concrete description (what it actually does, not 'streamlines your workflow')",
                "icon": "STRING — lucide-react icon name",
                "image": {
                    "url": "STRING — '' if not used",
                    "alt": "STRING — '' if not used",
                },
            },
        ],
    },

    "pricing_table": {
        "headline": "STRING — section headline, e.g. 'Plans for every team'",
        "subheadline": "STRING — 1 sentence intro, '' if not needed",
        "plans": [
            {
                "name": "STRING — plan name, e.g. 'Starter', 'Team', 'Business'",
                "price": "STRING — price string, e.g. '$0', '$29', 'Custom'",
                "period": "STRING — '/mo', '/year', or '' for custom",
                "description": "STRING — 1 sentence about who this plan is for",
                "features": ["STRING — feature line, no emoji or bullet char"],
                "cta": {
                    "label": "STRING — button text",
                    "href": "STRING",
                },
                "badge": "STRING — short label like 'Popular' or '' if none",
                "featured": "BOOLEAN — true for the recommended plan, false otherwise",
            },
        ],
    },

    "faq": {
        "headline": "STRING — e.g. 'Frequently asked questions'",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "question": "STRING — natural-language question",
                "answer": "STRING — 1-3 sentence answer",
            },
        ],
    },

    "testimonials": {
        "headline": "STRING — e.g. 'Loved by teams everywhere'",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "quote": "STRING — 18-30 word direct quote that SOUNDS like the audience speaks (use voice phrases); avoid 'great service, would recommend'",
                "name": "STRING — person's real-sounding name (not 'John Doe')",
                "role": "STRING — title, e.g. 'CTO' or 'Owner-Operator, 12 years'",
                "company": "STRING — company name",
                "avatar": {
                    "url": "STRING — '' if no photo",
                    "alt": "STRING — '' if no photo",
                },
            },
        ],
        "image_queries": "ARRAY[3-4]: STRING — one PORTRAIT query per testimonial item, parallel to items[]. E.g. 'professional portrait smiling cdl truck driver', 'female restaurant owner kitchen apron'. NOT 'business person headshot'.",
    },

    "cta_block": {
        "headline": "STRING — strong call-to-action headline",
        "subheadline": "STRING — 1-2 sentences of supporting copy",
        "primary_cta": {
            "label": "STRING — action verb",
            "href": "STRING",
        },
        "secondary_cta": {
            "label": "STRING — '' if not used",
            "href": "STRING — '' if not used",
        },
        "image": {
            "url": "STRING — '' (binder fills from image_queries[0] if present)",
            "alt": "STRING — '' if not used",
        },
        "image_queries": "ARRAY[0-1]: STRING — empty array if pure-text CTA; one query if you want a backdrop photo. Concrete subject, not 'business action'.",
    },

    "story": {
        "headline": "STRING — section headline",
        "body": "STRING — 2-4 paragraph narrative, separated by \\n\\n. PERSONAL voice, REAL details (year founded, city, what changed), not 'on a mission to revolutionize'",
        "image": {
            "url": "STRING — '' (binder fills from image_queries[0])",
            "alt": "STRING — '' if not used",
        },
        "milestones": [
            {
                "year": "STRING — e.g. '2021'",
                "label": "STRING — short milestone label",
                "body": "STRING — 1 sentence detail",
            },
        ],
        "image_queries": "ARRAY[1-2]: STRING — 1-2 photo queries matching the narrative (founders working, original location, etc.) — NOT 'team meeting'.",
    },

    "team": {
        "headline": "STRING — e.g. 'Meet the team'",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "name": "STRING — real-sounding full name",
                "role": "STRING — title",
                "bio": "STRING — 1-2 sentence bio with a specific detail (years exp, prior role, specialty)",
                "avatar": {
                    "url": "STRING",
                    "alt": "STRING",
                },
            },
        ],
        "image_queries": "ARRAY[3-6]: STRING — one portrait query per team member, parallel to items[]. Vary subjects to avoid stock-photo sameness.",
    },

    "contact": {
        "headline": "STRING — e.g. 'Get in touch'",
        "subheadline": "STRING — 1 sentence intro",
        "email": "STRING — contact email or ''",
        "phone": "STRING — phone number or ''",
        "address": "STRING — street address or ''",
        "hours": "STRING — opening hours or '' (e.g. 'Mon-Fri 9-6')",
        "primary_cta": {
            "label": "STRING — e.g. 'Send a message'",
            "href": "STRING — typically '#contact-form' or 'mailto:...'",
        },
    },

    "stats": {
        "headline": "STRING — '' if stats stand alone",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "value": "STRING — bold figure, e.g. '99.9%', '10K+', '$2M'",
                "label": "STRING — what the value measures",
                "subtext": "STRING — 1 short clarifier or ''",
            },
        ],
    },

    "logos": {
        "headline": "STRING — e.g. 'Trusted by teams at'",
        "items": [
            {
                "name": "STRING — company name",
                "image_url": "STRING — logo URL or ''",
                "link": "STRING — '' if not linked",
            },
        ],
    },

    "how_it_works": {
        "headline": "STRING — e.g. 'How it works'",
        "subheadline": "STRING — 1 sentence intro",
        "steps": [
            {
                "step_number": "STRING — '01', '02', etc.",
                "title": "STRING — step title, 2-4 words",
                "body": "STRING — 1-2 sentence step description",
                "icon": "STRING — lucide-react icon name",
            },
        ],
    },

    "newsletter": {
        "headline": "STRING — e.g. 'Stay in the loop'",
        "subheadline": "STRING — 1 sentence about what subscribers get",
        "cta_label": "STRING — submit button text",
        "placeholder": "STRING — input placeholder, e.g. 'you@company.com'",
    },

    "gallery": {
        "headline": "STRING — '' if gallery stands alone",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "image_url": "STRING — '' (binder fills from image_queries parallel index)",
                "caption": "STRING — '' if no caption",
                "alt": "STRING — descriptive alt text",
            },
        ],
        "image_queries": "ARRAY[4-8]: STRING — DISTINCT subject queries (no duplicates), parallel to items[]. Each must be a concrete subject noun, not a vibe word. E.g. 'kyoto temple sunset garden', 'sourdough loaf crumb closeup', 'workshop hand-stitched leather'.",
    },

    # ── Domain-specific section types that website_plan can emit ─────────
    # These were missing from the original schema set, which meant
    # expand_page_brief dropped them silently and Claude saw zero content
    # for restaurant menus, press lists, multi-location pages, etc.

    "menu": {
        "headline": "STRING — e.g. 'Dishes' or 'Today's menu'",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "title": "STRING — dish name (real-sounding, not 'Signature Burger')",
                "body": "STRING — 12-22 word ingredient sentence — proteins, sauce, side, technique",
                "price": "STRING — e.g. '$18' or '' if not relevant",
                "image": {
                    "url": "STRING — '' (binder fills)",
                    "alt": "STRING",
                },
            },
        ],
        "image_queries": "ARRAY[4-8]: STRING — one cuisine+dish query per item, parallel to items[]. E.g. 'spaghetti carbonara plated marble', 'wood-fired margherita pizza closeup'.",
    },

    "press": {
        "headline": "STRING — e.g. 'Recognized for excellence'",
        "subheadline": "STRING — '' or one plain sentence; NO pill words",
        "items": [
            {
                "title": "STRING — actual publication / award NAME (e.g. 'Eater NY', 'James Beard Foundation', 'The Infatuation') — NEVER an adjective like 'Authentic' or 'Passionate'",
                "body": "STRING — 8-16 word quote or accolade WITH attribution",
                "value": "STRING — year or rating (e.g. '2024', '4.7') or ''",
                "label": "STRING — publication name verbatim, same as title",
            },
        ],
    },

    "locations": {
        "headline": "STRING — e.g. 'Visit us'",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "name": "STRING — branch/location name",
                "address": "STRING — full street address",
                "phone": "STRING — phone number or ''",
                "hours": "STRING — e.g. 'Mon-Fri 9-6, Sat 10-4'",
                "directions_url": "STRING — Google Maps URL or ''",
            },
        ],
        "image_queries": "ARRAY[1-3]: STRING — storefront exterior queries, one per location ideally. E.g. 'brooklyn restaurant storefront brownstone'.",
    },

    "reservation": {
        "headline": "STRING — e.g. 'Reserve a table'",
        "subheadline": "STRING — 1 sentence intro",
        "instructions": "STRING — 1-2 sentence note about reservation policy or '' ",
        "primary_cta": {
            "label": "STRING — e.g. 'Book a table'",
            "href": "STRING — typically '#reservation-form' or external booking URL",
        },
        "fields_hint": "STRING — comma-separated form field names, e.g. 'name, email, party-size, date, time, notes'",
    },
}


# ── Aliases — map common synonyms to canonical type ──────────────────

SECTION_TYPE_ALIASES: dict[str, str] = {
    "value_props": "value_prop",
    "value-prop": "value_prop",
    "feature_grid": "features",
    "features_grid": "features",
    "services": "features",
    "capabilities": "features",
    "benefits": "value_prop",
    "pricing": "pricing_table",
    "plans": "pricing_table",
    "social_proof": "testimonials",
    "reviews": "testimonials",
    "cta": "cta_block",
    "call_to_action": "cta_block",
    "about": "story",
    "our_story": "story",
    "philosophy": "story",
    "mission": "story",
    "values": "story",
    "history": "story",
    "team_members": "team",
    "contact_form": "contact",
    "stats_block": "stats",
    "metrics": "stats",
    "logo_cloud": "logos",
    "trusted_by": "logos",
    "process": "how_it_works",
    "steps": "how_it_works",
    "journey": "how_it_works",
    "subscribe": "newsletter",
    "image_gallery": "gallery",
    # Domain-specific (now have dedicated schemas):
    "dishes": "menu",
    "featured_dishes": "menu",
    "menu_items": "menu",
    "awards": "press",
    "publications": "press",
    "press_features": "press",
    "branches": "locations",
    "stores": "locations",
    "reservations": "reservation",
    "booking": "reservation",
}


# ── Per-section descriptions for richer prompts ──────────────────────

SECTION_DESCRIPTIONS: dict[str, str] = {
    "hero": "Top-of-page introduction. Establishes what this page is about and the primary action.",
    "value_prop": "3-4 short bullets explaining the core value. Icons make them scannable.",
    "features": "Detailed product/service capabilities. More depth than value_prop.",
    "pricing_table": "Side-by-side plan comparison. Featured plan should stand out visually.",
    "faq": "Questions visitors actually ask. Each Q-A is concise and direct.",
    "testimonials": "Social proof from real customers. Quotes should sound human, not marketing-speak.",
    "cta_block": "Bold mid- or end-of-page conversion push. Single clear action.",
    "story": "Brand origin / about narrative. Personal voice, real details.",
    "team": "Faces of the people behind the brand. Builds trust on about pages.",
    "contact": "How to reach the company. Email, phone, address, hours, possibly a form CTA.",
    "stats": "Quantified achievements. Numbers should be specific and verifiable.",
    "logos": "Brand logos for social proof. Either customer logos or partner/integration logos.",
    "how_it_works": "Numbered process explanation. 3-5 steps that read in sequence.",
    "newsletter": "Email capture for marketing. Promise something specific in subscription value.",
    "gallery": "Visual showcase. Photos do the work; captions are optional supporting context.",
    "menu": "Restaurant / food menu. Each item is a real-sounding dish name + ingredient sentence + price.",
    "press": "Publications, awards, recognition. Title is the publication NAME (Eater NY, James Beard, etc.) — never an adjective.",
    "locations": "Multi-branch listings. Each entry is a real address + hours + phone.",
    "reservation": "Reservation / booking interface. Either a form or an external booking link.",
}


# ── Public helpers ───────────────────────────────────────────────────

def canonical_type(section_type: str) -> str:
    """Map any incoming type string to its canonical schema key.

    Lowercases, replaces spaces, dispatches through the alias table.
    Falls back to the raw input if nothing matches (caller decides).
    """
    norm = (section_type or "").strip().lower().replace(" ", "_").replace("-", "_")
    return SECTION_TYPE_ALIASES.get(norm, norm)


def schema_for(section_type: str) -> dict[str, Any] | None:
    """Return the JSON-shaped content schema for a section type, or None."""
    return SECTION_SCHEMAS.get(canonical_type(section_type))


def description_for(section_type: str) -> str:
    """Return the human-language description for a section type, or ''."""
    return SECTION_DESCRIPTIONS.get(canonical_type(section_type), "")


def known_section_types() -> list[str]:
    """Sorted list of canonical section types."""
    return sorted(SECTION_SCHEMAS.keys())


def schema_to_prompt_block(section_type: str) -> str:
    """Render a schema as a prompt-ready text block.

    Output format is JSON-flavored so Gemini and Claude can parse it,
    but the leaf values are description strings (the "STRING — ..."
    convention), making the contract human-readable too.

    Returns "" if the section type is unknown.
    """
    import json

    canon = canonical_type(section_type)
    schema = SECTION_SCHEMAS.get(canon)
    if not schema:
        return ""
    desc = SECTION_DESCRIPTIONS.get(canon, "")
    desc_line = f"# {canon} — {desc}\n" if desc else f"# {canon}\n"
    return desc_line + json.dumps(schema, indent=2, ensure_ascii=False)
