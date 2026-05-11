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
        "headline": "STRING — 5-10 words, bold and direct, the page's primary message",
        "subheadline": "STRING — 1-2 sentences supporting the headline",
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
            "url": "STRING — Unsplash/CDN URL or '' if no hero image",
            "alt": "STRING — descriptive alt text, '' if no image",
        },
    },

    "value_prop": {
        "headline": "STRING — section headline, 4-8 words",
        "subheadline": "STRING — 1 sentence elaboration, '' if not needed",
        "items": [
            {
                "title": "STRING — value bullet title, 2-4 words",
                "body": "STRING — 1-2 sentence explanation",
                "icon": "STRING — lucide-react icon name, e.g. 'Zap', 'Shield', 'Star'",
            },
        ],
    },

    "features": {
        "headline": "STRING — section headline, 4-8 words",
        "subheadline": "STRING — 1 sentence elaboration",
        "items": [
            {
                "title": "STRING — feature title",
                "body": "STRING — 1-2 sentence description",
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
                "quote": "STRING — 1-3 sentence testimonial",
                "name": "STRING — person's name",
                "role": "STRING — title, e.g. 'CTO'",
                "company": "STRING — company name",
                "avatar": {
                    "url": "STRING — '' if no photo",
                    "alt": "STRING — '' if no photo",
                },
            },
        ],
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
            "url": "STRING — '' if not used",
            "alt": "STRING — '' if not used",
        },
    },

    "story": {
        "headline": "STRING — section headline",
        "body": "STRING — 2-4 paragraph narrative, separated by \\n\\n",
        "image": {
            "url": "STRING — '' if not used",
            "alt": "STRING — '' if not used",
        },
        "milestones": [
            {
                "year": "STRING — e.g. '2021'",
                "label": "STRING — short milestone label",
                "body": "STRING — 1 sentence detail",
            },
        ],
    },

    "team": {
        "headline": "STRING — e.g. 'Meet the team'",
        "subheadline": "STRING — '' if not needed",
        "items": [
            {
                "name": "STRING — full name",
                "role": "STRING — title",
                "bio": "STRING — 1-2 sentence bio",
                "avatar": {
                    "url": "STRING",
                    "alt": "STRING",
                },
            },
        ],
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
                "image_url": "STRING — image URL",
                "caption": "STRING — '' if no caption",
                "alt": "STRING — descriptive alt text",
            },
        ],
    },
}


# ── Aliases — map common synonyms to canonical type ──────────────────

SECTION_TYPE_ALIASES: dict[str, str] = {
    "value_props": "value_prop",
    "value-prop": "value_prop",
    "feature_grid": "features",
    "features_grid": "features",
    "pricing": "pricing_table",
    "plans": "pricing_table",
    "social_proof": "testimonials",
    "reviews": "testimonials",
    "cta": "cta_block",
    "call_to_action": "cta_block",
    "about": "story",
    "our_story": "story",
    "team_members": "team",
    "contact_form": "contact",
    "stats_block": "stats",
    "metrics": "stats",
    "logo_cloud": "logos",
    "trusted_by": "logos",
    "process": "how_it_works",
    "steps": "how_it_works",
    "subscribe": "newsletter",
    "image_gallery": "gallery",
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
