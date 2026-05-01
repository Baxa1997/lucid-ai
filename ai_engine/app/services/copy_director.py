"""Copy Director — one Claude call that writes brand-specific copy for every slot.

Follows the same pattern as design_system_builder.py:
  - forced-JSON tool output (tool_choice)
  - schema with `required` fields
  - validator (bans generic filler; length caps)
  - one retry with violations as feedback
  - fail-soft (returns None → caller leaves copy to research/phase prompts)

Output is injected into research as ===COPY_DECK===. Phase 1/2/3 prompts read
it and must use those exact strings — no more "Learn More / Our Services /
Welcome to our platform" filler.

Scope: landing-family archetypes only. Admin/CRM/TMS use schema entity names
(already brand-specific) and don't have the same generic-filler problem.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger("lucid.copy_director")


# ─── Claude call constants ──────────────────────────────────────────────────

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4000
_TIMEOUT = 90.0


# ─── Banned generic filler ──────────────────────────────────────────────────
# If the model emits any of these (case-insensitive substring match), we count
# a violation and retry. This is the main defense against AI-generated tells.

_BANNED_PHRASES: tuple[str, ...] = (
    "learn more",
    "get started",
    "our services",
    "our features",
    "why choose us",
    "welcome to",
    "welcome to our",
    "your content here",
    "lorem ipsum",
    "premium quality",
    "best in class",
    "cutting edge",
    "industry-leading",
    "world-class solutions",
    "your one-stop",
    "one-stop shop",
    "subtitle",
    "heading 1",
    "placeholder",
    "description here",
    "headline goes here",
    "click here",
    "read more →",
)

# SaaS archetypes are allowed to use "Get Started" as a CTA — it's canonical
# there. For any other archetype it reads as generic template filler.
_SAAS_ARCHETYPES = {"saas_app", "saas_dashboard", "b2b_saas"}
_SAAS_EXEMPTIONS: tuple[str, ...] = ("get started",)


# ─── Tool (forced JSON schema) ──────────────────────────────────────────────

_COPY_TOOL = {
    "name": "emit_copy_deck",
    "description": (
        "Emit the complete, brand-specific copy deck for this project. Every "
        "string must feel written by the brand's copywriter — never generic "
        "template filler. Headlines are SHORT and PUNCHY (4-10 words). "
        "Subheads do the explaining (1-2 sentences, 15-30 words)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "hero": {
                "type": "object",
                "description": "The above-the-fold hero for the landing page.",
                "properties": {
                    "eyebrow":      {"type": "string", "description": "3-5 word tag that sits above the headline. Often all-caps. E.g. 'Small-batch roasters', 'Est. 2008 in Portland'. Optional but preferred."},
                    "headline":     {"type": "string", "description": "The hero headline. 4-10 words. No clichés. Must feel specific to THIS brand/domain."},
                    "subheadline":  {"type": "string", "description": "1-2 sentences, 15-30 words. Explains the promise concretely, with real detail — beans from which origin, which trainers, which neighborhood, which approach."},
                    "primary_cta":  {"type": "string", "description": "Action verb + noun, 2-4 words. Must match the domain (e.g. 'Reserve a Table', 'Browse the Menu', 'Book a Class'). NEVER 'Get Started' / 'Learn More' unless this is pure B2B SaaS."},
                    "secondary_cta":{"type": "string", "description": "Softer action. E.g. 'See Our Story', 'View Locations', 'Read Press'. Empty string if not needed."},
                },
                "required": ["headline", "subheadline", "primary_cta"],
            },
            "sections": {
                "type": "array",
                "description": "Copy for each content section on the landing page (testimonials, story, menu, features, locations, pricing, faq, cta — whatever the archetype calls for). 3-8 entries, matching the sections in the project schema.",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id":   {"type": "string", "description": "Matches the section type from project schema: hero, story, menu, features, testimonials, locations, gallery, pricing, faq, cta_final, etc."},
                        "eyebrow":      {"type": "string", "description": "Short category tag above the headline. E.g. 'Our Craft', 'What People Say'. 2-5 words."},
                        "headline":     {"type": "string", "description": "Section headline, 4-9 words. Specific to THIS brand. Never 'Our Services' / 'Why Choose Us'."},
                        "subheadline":  {"type": "string", "description": "1-2 sentences elaborating what this section is about, 15-30 words."},
                        "body_lead":    {"type": "string", "description": "Optional 1-sentence opener for prose-heavy sections (story, about). Empty if not applicable."},
                        "cta_primary":  {"type": "string", "description": "Optional CTA specific to this section. E.g. 'View Full Menu' on the menu teaser section. Empty if section has no CTA."},
                    },
                    "required": ["section_id", "headline", "subheadline"],
                },
            },
            "features": {
                "type": "array",
                "description": "Copy for features/benefits/values cards. 3-6 items. Each has a SPECIFIC title (not 'Premium Quality') and a 1-2 sentence description that would be true ONLY for this brand.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title":        {"type": "string", "description": "Feature title, 2-5 words. Concrete, not generic. Good: 'Slow-dripped for 18 hours'. Bad: 'Premium Quality'."},
                        "description":  {"type": "string", "description": "1-2 sentences, 15-30 words. A real, specific reason someone would care."},
                        "icon":         {"type": "string", "description": "Suggested lucide-react icon name (e.g. 'Flame', 'Leaf', 'Coffee'). Empty string if unsure."},
                    },
                    "required": ["title", "description"],
                },
            },
            "trust_strip": {
                "type": "array",
                "description": "Optional row of short credibility markers under hero or above footer. 3-6 short strings, max 30 chars each. E.g. ['Best Espresso 2024 — Eater', '4.9★ on Google', 'Featured in Bon Appétit']. Use only if the research mentions awards/press/ratings.",
                "items": {"type": "string"},
            },
            "microcopy": {
                "type": "object",
                "description": "Small UI strings that appear throughout the app. All must be brand-voiced, not default.",
                "properties": {
                    "search_placeholder":        {"type": "string", "description": "E.g. 'Find a roast…', 'Search classes'. Never 'Search…'."},
                    "newsletter_placeholder":    {"type": "string", "description": "E.g. 'your@email.com'. Keep default if unsure."},
                    "newsletter_cta":            {"type": "string", "description": "Subscribe button text. E.g. 'Join the list', 'Get the newsletter', 'Stay in the loop'. Not 'Subscribe'."},
                    "newsletter_success":        {"type": "string", "description": "Post-submit message, 1 sentence, brand-voiced."},
                    "contact_cta":               {"type": "string", "description": "Generic 'get in touch' link text. E.g. 'Say hello', 'Come visit us'. Not 'Contact Us'."},
                    "form_submit_primary":       {"type": "string", "description": "Primary form submit verb. E.g. 'Confirm Reservation', 'Start My Membership'. Must match domain."},
                    "form_submitting":           {"type": "string", "description": "Loading state text. E.g. 'Confirming…', 'Booking your table…'."},
                    "empty_state_headline":      {"type": "string", "description": "When a list is empty. E.g. 'No classes this week — try next week'. Brand-voiced."},
                },
                "required": ["form_submit_primary", "newsletter_cta"],
            },
            "footer": {
                "type": "object",
                "description": "Footer content strings.",
                "properties": {
                    "tagline":          {"type": "string", "description": "1-sentence brand tagline for under the logo, 6-14 words."},
                    "newsletter_pitch": {"type": "string", "description": "1-sentence newsletter pitch, 10-20 words. Never 'Subscribe to our newsletter'."},
                    "copyright_suffix": {"type": "string", "description": "What goes after '© YYYY Brand Name'. E.g. 'All roasts reserved.' (coffee), 'Built with care in Brooklyn.' Optional flavor."},
                },
                "required": ["tagline"],
            },
            "seo": {
                "type": "object",
                "description": "Meta tags for the landing page <head>.",
                "properties": {
                    "meta_title":        {"type": "string", "description": "HTML <title>, 50-65 chars. Brand name + tagline."},
                    "meta_description":  {"type": "string", "description": "Meta description, 140-160 chars. First-person voice is fine if the brand is."},
                },
                "required": ["meta_title", "meta_description"],
            },
        },
        "required": ["hero", "sections", "features", "microcopy", "footer", "seo"],
    },
}


# ─── System prompt ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a senior brand copywriter. Write copy that feels
authored by the brand's in-house team — never AI, never template filler.

HARD RULES:

1. BE SPECIFIC — every headline and subhead must reference something TRUE
   about the domain or brand archetype. If you can copy-paste the headline
   into a competitor's website without changing meaning, it's wrong.
   ✗ "Experience Excellence in Every Sip"
   ✓ "Single-origin beans, roasted Tuesday at sunrise."

2. SHORT HEADLINES, EXPLANATORY SUBHEADS.
   Headlines: 4-10 words, punchy, no lists, no "and also".
   Subheadlines: 15-30 words, do the actual explaining.

3. BANNED PHRASES — do not emit any of these, in any form:
   - "Learn More" (use the action verb for the thing you're linking to)
   - "Get Started" (unless the project is pure B2B SaaS)
   - "Our Services" / "Our Features" / "Why Choose Us"
   - "Welcome to [anything]"
   - "Lorem ipsum" / "Your content here" / "Placeholder" / "Subtitle"
   - "Premium Quality" / "Best in class" / "Cutting-edge" / "World-class"
   - "One-stop shop" / "Your one-stop"
   - "Click here" / "Read more →"

4. CTA VERBS MATCH THE DOMAIN.
   Restaurant/café → "Reserve a Table", "Order Online", "See the Menu"
   Fitness/gym → "Book a Class", "Start Free Week", "Meet the Trainers"
   Hotel → "Book Your Stay", "Check Availability"
   Real estate → "Browse Listings", "Tour a Home"
   Portfolio → "Start a Project", "Let's Talk", "See the Work"
   Wedding/event → "Check Availability", "Request Pricing"
   Nonprofit → "Donate Today", "Volunteer"
   Ecommerce → "Shop the Collection", "Add to Bag"
   B2B SaaS → "Start Free", "Book a Demo" (SaaS is the only place
   "Get Started" is OK; even there, prefer more specific)

5. VOICE FOLLOWS THE BRAND.
   Warm-artisan (coffee, bakery, florist): conversational, sensory detail,
     first-person plural sometimes ("We roast small batches…").
   Editorial-luxe (hotel, wedding, restaurant): precise, unhurried, imagery.
   Tech-crisp (SaaS, tool, API): direct, benefit-forward, no fluff.
   Playful-retro (kids, games, food trucks): punchy, fun, exclamation marks OK.
   Follow the copy_tone passed in the user message. If none, infer from domain.

6. FEATURES ARE CONCRETE.
   ✗ "Premium Quality"           ✓ "Beans roasted 48 hours ago"
   ✗ "Expert Team"                ✓ "4 baristas, 2 SCA-certified"
   ✗ "Great Value"                ✓ "$4 drip, $5.50 espresso"
   ✗ "Fast Shipping"              ✓ "Roast-to-doorstep in 3 days"
   ✗ "24/7 Support"               ✓ "Same-hour response during service"
   Every feature description must contain a NUMBER, a PROPER NOUN, or a
   concrete sensory detail.

7. NEVER LOREM IPSUM. Never placeholder. Never "TODO". Every string ships.

Output ONLY via the provided tool. No prose, no explanation.
"""


# ─── User-prompt builder ────────────────────────────────────────────────────

def _build_user_prompt(
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    vibe: str,
    section_ids: list[str],
    cultural_atmosphere: str = "",
    violations: Optional[list[str]] = None,
) -> str:
    lines: list[str] = []
    lines.append("Write the full copy deck for this project.")
    lines.append("")
    lines.append(f"BRAND_NAME: {brand_name or '(infer from description)'}")
    lines.append(f"DOMAIN: {domain or '(unspecified)'}")
    lines.append(f"LAYOUT_ARCHETYPE: {layout_archetype}")
    lines.append(f"COPY_TONE (from research): {copy_tone or '(use your judgment from domain)'}")
    lines.append(f"VIBE: {vibe or '(use your judgment from domain)'}")
    lines.append(f"PROJECT_DESCRIPTION: {description[:800]}")
    lines.append("")
    if cultural_atmosphere and cultural_atmosphere.strip() and "none" not in cultural_atmosphere[:80].lower():
        lines.append("CULTURAL_ATMOSPHERE (apply when writing copy):")
        lines.append(cultural_atmosphere.strip())
        lines.append("")
        lines.append("Cultural copy rules:")
        lines.append("  • Use language_phrases as section labels / eyebrows / accent words.")
        lines.append("    Keep them in the source language — DO NOT translate.")
        lines.append("    Example: 'Antipasti / Primi / Secondi / Dolci' as menu sections,")
        lines.append("    'La Famiglia' as About header, 'Benvenuti' as welcome eyebrow.")
        lines.append("  • Apply section_label_overrides — rename 'Menu' to 'La Carta',")
        lines.append("    'Reservations' to 'Prenotazioni', etc. when a mapping exists.")
        lines.append("  • Match cultural_voice_overlay tone — warm/familial/quiet/precise per culture.")
        lines.append("  • CTAs may include a culturally-flavored phrase ('Prenota un Tavolo'")
        lines.append("    rather than 'Reserve a Table') when appropriate.")
        lines.append("  • Microcopy may include source-language honorifics or interjections.")
        lines.append("")
    if section_ids:
        lines.append(
            "SECTIONS TO COVER (one copy block per id, in this order):"
        )
        for sid in section_ids:
            lines.append(f"  - {sid}")
        lines.append("")
    lines.append(
        "Write the copy deck now. Every string must pass the HARD RULES — "
        "specific, short headlines, banned phrases forbidden, CTAs matching "
        "the domain, features concrete with numbers or proper nouns."
    )
    if violations:
        lines.append("")
        lines.append("PRIOR ATTEMPT VIOLATIONS — fix these, then try again:")
        for v in violations[:12]:
            lines.append(f"  ! {v}")
    return "\n".join(lines)


# ─── Validation ─────────────────────────────────────────────────────────────

def _contains_banned(text: str, archetype: str) -> Optional[str]:
    """Return the first banned phrase found, or None. Skips SaaS-exempt phrases for SaaS archetypes."""
    if not text:
        return None
    lt = text.lower()
    is_saas = (archetype or "").lower() in _SAAS_ARCHETYPES
    for phrase in _BANNED_PHRASES:
        if is_saas and phrase in _SAAS_EXEMPTIONS:
            continue
        if phrase in lt:
            return phrase
    return None


def _count_words(s: str) -> int:
    return len((s or "").strip().split())


def validate_copy_deck(deck: dict, layout_archetype: str = "") -> list[str]:
    """Return human-readable violation strings. Empty list = pass."""
    if not deck:
        return ["copy_deck is empty"]
    violations: list[str] = []

    # ── Hero ─────────────────────────────────────────────────────────────
    hero = deck.get("hero") or {}
    h_headline = hero.get("headline", "")
    h_sub = hero.get("subheadline", "")
    h_cta = hero.get("primary_cta", "")
    if not h_headline:
        violations.append("hero.headline is empty")
    elif _count_words(h_headline) > 12:
        violations.append(
            f"hero.headline is too long ({_count_words(h_headline)} words) — target 4-10."
        )
    elif _count_words(h_headline) < 3:
        violations.append(
            f"hero.headline is too short ({_count_words(h_headline)} words) — target 4-10."
        )

    if not h_sub:
        violations.append("hero.subheadline is empty")
    elif _count_words(h_sub) > 40:
        violations.append(
            f"hero.subheadline is too long ({_count_words(h_sub)} words) — target 15-30."
        )

    if not h_cta:
        violations.append("hero.primary_cta is empty")
    elif _count_words(h_cta) > 5:
        violations.append(
            f"hero.primary_cta is too long ({_count_words(h_cta)} words) — target 2-4."
        )

    for field_name, val in (
        ("hero.headline", h_headline),
        ("hero.subheadline", h_sub),
        ("hero.primary_cta", h_cta),
        ("hero.eyebrow", hero.get("eyebrow", "")),
        ("hero.secondary_cta", hero.get("secondary_cta", "")),
    ):
        banned = _contains_banned(val, layout_archetype)
        if banned:
            violations.append(
                f"{field_name} contains banned phrase '{banned}': '{val[:80]}'"
            )

    # ── Sections ─────────────────────────────────────────────────────────
    sections = deck.get("sections") or []
    if len(sections) < 2:
        violations.append(
            f"sections has only {len(sections)} entries — need at least 2."
        )
    seen_headlines: set[str] = set()
    for i, s in enumerate(sections):
        headline = s.get("headline", "")
        if not headline:
            violations.append(f"sections[{i}].headline is empty")
            continue
        hl = headline.strip().lower()
        if hl in seen_headlines:
            violations.append(
                f"sections[{i}].headline duplicates an earlier headline: '{headline}'"
            )
        seen_headlines.add(hl)
        if _count_words(headline) > 12:
            violations.append(
                f"sections[{i}].headline is too long: '{headline[:50]}…'"
            )
        for field_name, val in (
            (f"sections[{i}].headline", headline),
            (f"sections[{i}].subheadline", s.get("subheadline", "")),
            (f"sections[{i}].cta_primary", s.get("cta_primary", "")),
            (f"sections[{i}].eyebrow", s.get("eyebrow", "")),
        ):
            banned = _contains_banned(val, layout_archetype)
            if banned:
                violations.append(
                    f"{field_name} contains banned phrase '{banned}'"
                )

    # ── Features ─────────────────────────────────────────────────────────
    features = deck.get("features") or []
    if len(features) < 3:
        violations.append(
            f"features has only {len(features)} entries — need at least 3."
        )
    for i, f in enumerate(features):
        title = f.get("title", "")
        desc = f.get("description", "")
        if not title:
            violations.append(f"features[{i}].title is empty")
            continue
        banned = _contains_banned(title, layout_archetype) or _contains_banned(desc, layout_archetype)
        if banned:
            violations.append(
                f"features[{i}] contains banned phrase '{banned}': '{title[:50]}'"
            )
        # Concrete-detail heuristic: feature description must contain a digit
        # OR a proper noun (capitalized word that isn't the first word) OR be
        # longer than a vague 8-word stub.
        if desc:
            has_digit = any(ch.isdigit() for ch in desc)
            words = desc.split()
            has_proper_noun = any(
                w[:1].isupper() for w in words[1:]  # skip first word
            )
            if len(words) < 8 and not has_digit and not has_proper_noun:
                violations.append(
                    f"features[{i}].description too vague — add a number, proper noun, "
                    f"or concrete detail: '{desc[:60]}'"
                )

    # ── Microcopy ────────────────────────────────────────────────────────
    mc = deck.get("microcopy") or {}
    for field_name, val in mc.items():
        banned = _contains_banned(val, layout_archetype)
        if banned:
            violations.append(
                f"microcopy.{field_name} contains banned phrase '{banned}'"
            )

    # ── Footer ───────────────────────────────────────────────────────────
    footer = deck.get("footer") or {}
    tagline = footer.get("tagline", "")
    if not tagline:
        violations.append("footer.tagline is empty")
    banned = _contains_banned(tagline, layout_archetype)
    if banned:
        violations.append(f"footer.tagline contains banned phrase '{banned}'")

    # ── SEO ──────────────────────────────────────────────────────────────
    seo = deck.get("seo") or {}
    mt = seo.get("meta_title", "")
    md = seo.get("meta_description", "")
    if not mt:
        violations.append("seo.meta_title is empty")
    if not md:
        violations.append("seo.meta_description is empty")
    elif len(md) > 200:
        violations.append(
            f"seo.meta_description too long ({len(md)} chars) — keep ≤160."
        )

    return violations


# ─── Claude caller ──────────────────────────────────────────────────────────

async def _call_claude(system: str, user: str, api_key: str, user_id: str | None = None, websocket=None) -> Optional[dict]:
    """Call Claude with automatic retry on rate limits / 5xx / network errors.

    Returns the tool_use input dict on success, or None on permanent failure
    (bad request, auth error, exhausted retries). Transient errors are
    transparently retried up to 3 times with exponential backoff.
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
        "temperature": 0.55,  # some variance so copy doesn't repeat across runs
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [_COPY_TOOL],
        "tool_choice": {"type": "tool", "name": "emit_copy_deck"},
    }

    async def _do_call() -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_API_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, resp.text)
        return resp.json()

    try:
        data = await call_with_retry(_do_call, label="copy_director", websocket=websocket)
    except LLMPermanentError as exc:
        logger.warning("Copy Director permanent failure: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Copy Director failed after retries: %s", exc)
        return None

    # Meter token usage (fire-and-forget)
    try:
        from app.services.billing_meter import report_token_usage
        _u = data.get("usage") or {}
        report_token_usage(
            user_id,
            int(_u.get("input_tokens", 0) or 0),
            int(_u.get("output_tokens", 0) or 0),
            source="copy_director",
        )
    except Exception:
        pass

    try:
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "emit_copy_deck":
                return block.get("input") or {}
    except Exception as exc:
        logger.warning("Copy Director response parse failed: %s", exc)
    return None


# ─── Public orchestrator ────────────────────────────────────────────────────

async def build_copy_deck(
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    section_ids: list[str],
    api_key: str,
    vibe: str = "",
    cultural_atmosphere: str = "",
    websocket=None,
    user_id: str | None = None,
) -> Optional[dict]:
    """One Claude call that writes a brand-specific copy deck.

    Retries once with violation feedback. Returns None if both attempts fail
    — caller leaves copy generation to Phase 1/2 prompts.

    FAIL-SOFT: any exception logs a warning and returns None.
    """
    user = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        section_ids=section_ids or [],
        cultural_atmosphere=cultural_atmosphere,
    )

    # Attempt 1
    try:
        deck = await _call_claude(_SYSTEM_PROMPT, user, api_key, user_id=user_id, websocket=websocket)
    except Exception as exc:
        logger.warning("Copy Director attempt 1 crashed: %s", exc)
        return None
    if not deck:
        return None

    violations = validate_copy_deck(deck, layout_archetype=layout_archetype)
    if not violations:
        logger.info(
            "Copy Director OK on attempt 1 — hero='%s', %d sections, %d features",
            (deck.get("hero") or {}).get("headline", "")[:60],
            len(deck.get("sections") or []),
            len(deck.get("features") or []),
        )
        return deck

    logger.info(
        "Copy Director attempt 1 had %d violations, retrying with feedback",
        len(violations),
    )
    for _v in violations:
        logger.info("Copy Director violation: %s", _v)
    # Attempt 2 with violations as feedback
    user2 = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        section_ids=section_ids or [],
        cultural_atmosphere=cultural_atmosphere,
        violations=violations,
    )
    try:
        deck2 = await _call_claude(_SYSTEM_PROMPT, user2, api_key, user_id=user_id, websocket=websocket)
    except Exception as exc:
        logger.warning("Copy Director attempt 2 crashed: %s", exc)
        return None
    if not deck2:
        return None

    violations2 = validate_copy_deck(deck2, layout_archetype=layout_archetype)
    if not violations2:
        logger.info("Copy Director OK on attempt 2 (after %d initial violations)", len(violations))
        return deck2
    logger.warning(
        "Copy Director attempt 2 still had %d violations — returning anyway; "
        "phase prompts will overwrite if needed: %s",
        len(violations2), violations2[:5],
    )
    # Return the second attempt even with violations — better than None, since
    # Copy Director is additive; phase prompts still run as a safety net.
    return deck2


# ─── Rendering: deck → research text block ──────────────────────────────────

def _render_copy_deck(deck: dict) -> str:
    """Render the copy deck as a compact text block Claude can parse in code-gen."""
    lines: list[str] = []
    hero = deck.get("hero") or {}
    lines.append("HERO:")
    if hero.get("eyebrow"):
        lines.append(f"  eyebrow: {hero['eyebrow']}")
    lines.append(f"  headline: {hero.get('headline', '')}")
    lines.append(f"  subheadline: {hero.get('subheadline', '')}")
    lines.append(f"  primary_cta: {hero.get('primary_cta', '')}")
    if hero.get("secondary_cta"):
        lines.append(f"  secondary_cta: {hero['secondary_cta']}")
    lines.append("")

    lines.append("SECTIONS (match section_id to your generated section component):")
    for s in (deck.get("sections") or []):
        lines.append(f"  - section_id: {s.get('section_id', '')}")
        if s.get("eyebrow"):
            lines.append(f"    eyebrow: {s['eyebrow']}")
        lines.append(f"    headline: {s.get('headline', '')}")
        if s.get("subheadline"):
            lines.append(f"    subheadline: {s['subheadline']}")
        if s.get("body_lead"):
            lines.append(f"    body_lead: {s['body_lead']}")
        if s.get("cta_primary"):
            lines.append(f"    cta_primary: {s['cta_primary']}")
    lines.append("")

    features = deck.get("features") or []
    if features:
        lines.append("FEATURES (use these as feature-card titles + descriptions):")
        for f in features:
            icon = f" [icon: {f['icon']}]" if f.get("icon") else ""
            lines.append(f"  - {f.get('title', '')}{icon}")
            lines.append(f"    {f.get('description', '')}")
        lines.append("")

    trust = deck.get("trust_strip") or []
    if trust:
        lines.append("TRUST_STRIP (optional — use as small credibility markers):")
        for t in trust:
            lines.append(f"  - {t}")
        lines.append("")

    mc = deck.get("microcopy") or {}
    if mc:
        lines.append("MICROCOPY (replace default UI strings with these exact values):")
        for k, v in mc.items():
            if v:
                lines.append(f"  {k}: {v}")
        lines.append("")

    footer = deck.get("footer") or {}
    if footer:
        lines.append("FOOTER:")
        for k, v in footer.items():
            if v:
                lines.append(f"  {k}: {v}")
        lines.append("")

    seo = deck.get("seo") or {}
    if seo:
        lines.append("SEO:")
        if seo.get("meta_title"):
            lines.append(f"  meta_title: {seo['meta_title']}")
        if seo.get("meta_description"):
            lines.append(f"  meta_description: {seo['meta_description']}")
        lines.append("")

    lines.append(
        "INSTRUCTION — USE THE EXACT STRINGS ABOVE in the generated code. "
        "Every hero headline/subhead/CTA, every section headline, every "
        "feature title/description, every microcopy string is fixed by this "
        "deck. DO NOT rewrite them to be shorter, cuter, or more generic. "
        "DO NOT emit 'Learn More', 'Our Services', 'Welcome to', 'Get "
        "Started' (unless this is B2B SaaS), 'Lorem ipsum', or any other "
        "template filler. If a section component needs a string this deck "
        "doesn't cover (e.g. FAQ questions), write it in the same voice and "
        "level of specificity."
    )
    return "\n".join(lines)


def inject_copy_deck(research: str, deck: dict) -> str:
    """Inject/replace the ===COPY_DECK=== block in research."""
    if not research or not deck:
        return research

    header = "===COPY_DECK==="
    # Remove existing block if present
    stripped = research
    while header in stripped:
        start = stripped.index(header)
        rest = stripped[start + len(header):]
        next_header_pos = rest.find("===")
        if next_header_pos == -1:
            stripped = stripped[:start].rstrip()
        else:
            stripped = stripped[:start] + rest[next_header_pos:]

    block = f"{header}\n{_render_copy_deck(deck)}"
    # Prepend so distiller keeps it even if research is truncated downstream.
    return f"{block}\n\n{stripped.lstrip()}"
