"""Required-section blueprint per primary_purpose.

Consumed by Stage 7.5 (content audit). For each purpose we list the
section "concepts" a finished site needs — names are matched loosely
against generated section type strings AND component filenames so we
don't false-positive on minor naming variation (e.g. "open_roles" vs
"openpositions" vs "jobOpenings" all satisfy the recruitment "openings"
requirement).

Concepts are tuples of synonyms: a concept is satisfied when ANY synonym
appears in ANY section type OR component filename on the site.

Kept deliberately MINIMAL — the user told us false positives are worse
than misses.
"""
from __future__ import annotations

from typing import Iterable


# concept_id → tuple of synonyms (lowercased, no punctuation)
# A concept is satisfied when any synonym is a substring of any
# section type or component file basename in the generated site.
_PURPOSE_REQUIREMENTS: dict[str, list[tuple[str, ...]]] = {
    "recruitment": [
        ("hero",),
        ("openrole", "openposition", "jobopening", "jobs", "roles", "careers", "positions"),
        ("apply", "application", "applicationform", "applyform"),
    ],
    "lead_generation": [
        ("hero",),
        ("contact", "contactform", "inquiry", "getintouch", "quote", "consultation"),
        ("cta", "calltoaction"),
    ],
    "ecommerce": [
        ("hero",),
        ("product", "shop", "store", "catalog", "collection"),
        ("cart", "checkout", "buy", "addtocart"),
    ],
    "appointment_booking": [
        ("hero",),
        ("service", "services", "offerings"),
        ("book", "booking", "schedule", "appointment", "reservation", "reserve"),
    ],
    "event_registration": [
        ("hero",),
        ("event", "schedule", "agenda", "speakers"),
        ("register", "registration", "rsvp", "tickets"),
    ],
    "education": [
        ("hero",),
        ("course", "lesson", "program", "curriculum", "class"),
    ],
    "fundraising": [
        ("hero",),
        ("cause", "mission", "impact", "story"),
        ("donate", "donation", "give", "support", "contribute"),
    ],
    "community": [
        ("hero",),
        ("join", "members", "signup", "register", "community"),
    ],
    "product_showcase": [
        ("hero",),
        ("product", "portfolio", "work", "showcase", "gallery", "projects"),
    ],
    "brand_awareness": [
        ("hero",),
        # Brand awareness is intentionally permissive — a hero + anything else.
    ],
}


def get_required_sections(primary_purpose: str | None) -> list[tuple[str, ...]]:
    """Return the list of required concepts for a primary_purpose.

    Each concept is a tuple of synonyms; presence of ANY synonym
    satisfies that concept. Unknown / missing purpose → ``[("hero",)]``
    (very permissive default) so the audit never blows up unknown sites.
    """
    if not primary_purpose:
        return [("hero",)]
    return _PURPOSE_REQUIREMENTS.get(primary_purpose.strip().lower(), [("hero",)])


def concept_satisfied(concept: Iterable[str], haystack: str) -> bool:
    """True when any synonym in ``concept`` appears as a substring of haystack.

    Haystack should be one big lowercased string built by the caller
    (e.g. all section types + component basenames joined with spaces).
    """
    return any(syn.lower() in haystack for syn in concept)


def primary_concept_label(concept: Iterable[str]) -> str:
    """Pick the first (most canonical) synonym as the human-readable label."""
    for syn in concept:
        return syn
    return "section"
