"""Image binder for the new landing flow.

Walks `src/content/landing.json`, runs ONE Unsplash search per `query`
in parallel, and patches `url` into every section's `images[]` array.

Why this lives separately from the legacy image binder:
  • The legacy binder edits JSX import statements; this binder edits a
    runtime JSON file. Editing JSON keeps images data — swapping a photo
    after generation is a JSON patch, not a regen.
  • Hero sections get hero-resolution URLs (1600×900). Other sections
    get card-resolution URLs (800×600) — keeps payload light.
  • Failures are silent: empty url means the section renders nothing or
    a placeholder. Generation never blocks on Unsplash.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Number of Unsplash candidates to request per query so we can pick a
# project-stable offset into the list. 5 is enough variety to avoid
# collisions across briefs with the same query without burning rate budget.
_CANDIDATE_POOL = 5


def _project_offset(seed: str, query: str, modulo: int) -> int:
    """Deterministic offset into the Unsplash result list.

    Same project + same query → same photo (so re-running a generation
    doesn't shuffle hero images on the user). Different project + same
    query → different photo (so two real-estate briefs don't share the
    same Unsplash top hit).
    """
    if modulo <= 1:
        return 0
    h = hashlib.md5(f"{seed}::{query}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % modulo


# Abstract single-word queries return random unrelated stock photos. We either
# strip the word and rely on enrichment, or refuse to search at all.
_ABSTRACT_WORDS = {
    "concept", "philosophy", "vision", "story", "intention", "essence",
    "spirit", "soul", "ethos", "approach", "experience", "ambience",
    "atmosphere", "feeling", "mood", "vibe", "presence", "moment",
    "press", "logos", "publication", "publications", "media", "brand",
    "identity", "values", "mission", "promise", "principles", "purpose",
    "craft", "passion", "tradition", "heritage",
}

# Section types that should never resolve an Unsplash image (they render text/logo
# strips instead — Unsplash returns garbage for these queries).
_NO_IMAGE_TYPES = {"press", "logos", "publications", "awards", "faq",
                   "stats", "newsletter", "contact_form", "reservation",
                   "booking_form", "contact"}


def _strip_placeholders(q: str) -> str:
    """Remove {curly-brace placeholders} and surrounding braces from a query.

    Brief prompt forbids them, but Gemini occasionally leaks `{adjective}` tokens
    into image_query. Drop the braces, keep the inner text — Unsplash still
    indexes the words.
    """
    return re.sub(r"\{+\s*([^{}]*?)\s*\}+", r"\1", q or "").strip()


# Per-section-type query templates: when the Brief writes a generic query for one
# of these section types, we sanitise + re-anchor it. The lambda gets (raw_query,
# domain_terms, item_title) and returns the search string sent to Unsplash.
_SECTION_TEMPLATES: dict[str, str] = {
    # cuisine cards — anchor with cuisine + "dish food plate" if generic
    "menu":          "{q} {cuisine} dish food plated",
    "dishes":        "{q} {cuisine} dish food plated",
    "featured_dishes": "{q} {cuisine} dish food plated",
    # hospitality
    "rooms":         "{q} hotel suite interior bed",
    "accommodations":"{q} hotel suite interior",
    "stays":         "{q} hotel suite interior",
    # team / chefs — keep portrait orientation
    "team":          "{q} portrait professional",
    "chefs":         "{q} chef kitchen portrait",
    # gallery / experience — anchor with cuisine/domain
    "gallery":       "{q} {cuisine}",
    "experience":    "{q} {cuisine}",
    "experiences":   "{q} {cuisine}",
    "events":        "{q} {cuisine} event venue",
}


def _apply_template(stype: str, query: str, cuisine: str) -> str:
    tpl = _SECTION_TEMPLATES.get(stype.lower())
    if not tpl:
        return query
    return tpl.format(q=query, cuisine=cuisine).strip()


async def bind_landing_images(
    workspace_path: str,
    *,
    fallback_keywords: list[str] | None = None,
    websocket: Any = None,
) -> dict[str, int]:
    """Read landing.json, fetch images per query, write back.

    Returns counts: {requested, bound}. Fail-soft on every error.
    """
    target = os.path.join(workspace_path, "src", "content", "landing.json")
    if not os.path.exists(target):
        logger.warning("bind_landing_images: %s missing — skipping", target)
        return {"requested": 0, "bound": 0}

    try:
        with open(target, "r", encoding="utf-8") as fh:
            content = json.load(fh)
    except Exception as exc:
        logger.warning("bind_landing_images: load failed (%s) — skipping", exc)
        return {"requested": 0, "bound": 0}

    if not os.environ.get("UNSPLASH_ACCESS_KEY"):
        logger.info("bind_landing_images: UNSPLASH_ACCESS_KEY missing — leaving urls empty")
        return {"requested": 0, "bound": 0}

    sections = content.get("sections") or []

    # Build a domain-context suffix to enrich every image query so generic
    # words like "interior", "ambience", "concept" don't return random stock
    # photos (e.g. a MUJI store for a Japanese restaurant). Pulled from the
    # Brief's brand description + motif + domain_keywords.
    brand_block = content.get("brand") or {}
    theme = content.get("theme") or {}
    motif = (theme.get("motif") or "").strip().lower()
    description = (brand_block.get("description") or "").lower()
    name = (brand_block.get("name") or "").lower()
    domain_terms: list[str] = []

    # Cuisine / hospitality domain inference. Each entry: (any-of-keywords, anchor-words).
    _DOMAIN_HINTS = [
        (("omakase", "sushi", "sashimi", "nigiri", "kaiseki", "ramen", "izakaya", "japanese"),
         ["japanese", "sushi"]),
        (("italian", "pasta", "pizza", "trattoria", "osteria"), ["italian", "pasta"]),
        (("french", "bistro", "patisserie"), ["french", "bistro"]),
        (("mexican", "taqueria", "taco"), ["mexican", "taco"]),
        (("indian", "tandoor", "biryani"), ["indian", "curry"]),
        (("thai",), ["thai", "asian"]),
        (("chinese", "dim sum"), ["chinese", "asian"]),
        (("steakhouse", "grill"), ["steakhouse", "grill"]),
        (("seafood",), ["seafood"]),
        (("vegan", "plant-based"), ["vegan"]),
        (("hotel", "resort", "spa", "boutique"), ["hotel", "interior"]),
        (("cafe", "café", "coffee"), ["cafe", "coffee"]),
        (("bakery", "patisserie"), ["bakery", "pastry"]),
        (("yoga", "studio"), ["yoga", "studio"]),
        (("gym", "fitness"), ["gym", "fitness"]),
        (("salon", "barber"), ["salon"]),
        (("portfolio", "agency", "studio"), ["studio"]),
    ]
    haystack = f"{name} {description} {motif}".strip()
    for keywords, anchors in _DOMAIN_HINTS:
        if any(k in haystack for k in keywords):
            domain_terms.extend(anchors)
            break
    # Always also append the first 2 domain_keywords from the brief.
    for k in (fallback_keywords or [])[:2]:
        k = (k or "").strip().lower()
        if k and k not in domain_terms:
            domain_terms.append(k)
    domain_suffix = " ".join(domain_terms[:3]).strip()
    cuisine_anchor = " ".join(domain_terms[:2]).strip() or "restaurant"

    # Project-stable seed for the per-photo offset. Brand name is project-unique
    # (each generation gets its own brand), tagline adds entropy so two brands
    # that happen to share a name (e.g. "Elite Estates") still pick differently.
    project_seed = f"{name}|{(brand_block.get('tagline') or '')[:80]}".strip().lower()

    def _sanitise(q: str) -> str:
        """Drop curly-brace placeholders, brand-name tokens, and pure-abstract words."""
        q = _strip_placeholders(q)
        # Trim any obvious brand-name tokens that pollute Unsplash searches.
        brand_tokens = {t.lower() for t in name.split() if len(t) > 2}
        tokens = [t for t in q.split() if t.lower() not in brand_tokens]
        # Drop tokens that are *purely* abstract (single-word abstract = useless).
        # We keep abstract words when they sit alongside concrete nouns.
        if len(tokens) <= 1 and tokens and tokens[0].lower() in _ABSTRACT_WORDS:
            tokens = []
        return " ".join(tokens).strip()

    def _enrich(q: str, is_hero: bool, stype: str) -> str:
        """Sanitise the query, apply per-section template, then append domain anchors.

        Hero queries are usually already specific (the Brief writes them with
        full subject context), so we don't double-enrich them — only sanitise.
        """
        cleaned = _sanitise(q) or q
        if not is_hero:
            cleaned = _apply_template(stype, cleaned, cuisine_anchor)
        if is_hero or not domain_suffix:
            return cleaned
        ql = cleaned.lower()
        if any(t and t in ql for t in domain_terms):
            return cleaned
        return f"{cleaned} {domain_suffix}".strip()

    # Per-item image_query synthesis ────────────────────────────────────
    # Gemini's brief frequently puts image queries on `items[i].image_query`
    # without also seeding a parallel `section.images[i]` placeholder.
    # The codegen prompt tells Claude that per-item images live at
    # `section.images[i].url` (parallel-indexed with items), so without
    # this synthesis the JSX renders a hardcoded fallback or a broken `?`.
    # Mirror item queries into images[] so the existing fetch loop covers
    # them. We only fill empty / missing slots — never overwrite a URL the
    # brief already pinned.
    for section in sections:
        items = section.get("items") or []
        if not isinstance(items, list) or not items:
            continue
        existing_images = section.get("images") or []
        if not isinstance(existing_images, list):
            existing_images = []
        synthesized_any = False
        for it_idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            iq = (item.get("image_query") or "").strip()
            if not iq:
                continue
            while len(existing_images) <= it_idx:
                existing_images.append({})
            slot = existing_images[it_idx]
            if not isinstance(slot, dict):
                continue
            if not slot.get("url"):
                slot.setdefault("query", iq)
                slot.setdefault("alt", item.get("title") or iq)
                synthesized_any = True
        if synthesized_any:
            section["images"] = existing_images

    # Collect every (section_idx, image_idx, query, is_hero) tuple
    jobs: list[tuple[int, int, str, bool]] = []
    for s_idx, section in enumerate(sections):
        stype = (section.get("type") or "").lower()
        is_hero = stype == "hero"
        # Press / logos / FAQ etc render as text — Unsplash returns junk for them.
        if stype in _NO_IMAGE_TYPES:
            section["images"] = []
            continue
        images = section.get("images") or []
        for i_idx, img in enumerate(images):
            if not isinstance(img, dict):
                continue
            if img.get("url"):
                continue  # already bound — skip
            query = (img.get("query") or img.get("alt") or "").strip()
            if not query and fallback_keywords:
                query = fallback_keywords[0]
            if not query:
                continue
            query = _enrich(query, is_hero, stype)
            jobs.append((s_idx, i_idx, query, is_hero))

    if not jobs:
        logger.info("bind_landing_images: no image queries — done")
        return {"requested": 0, "bound": 0}

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"🖼️  Binding {len(jobs)} images...",
            })
        except Exception:
            pass

    sem = asyncio.Semaphore(8)

    def _simplify(q: str) -> str:
        """Build a broader retry query: keep last 2-3 concrete tokens.

        Unsplash returns nothing when a query is too long / too specific (e.g.
        "Patatas Bravas tapas restaurant spanish dish food plated"). Trim back
        to the most concrete tail tokens — this typically still returns the
        intended dish without losing the cuisine anchor.
        """
        toks = [t for t in q.split() if t.lower() not in _ABSTRACT_WORDS]
        # Keep the cuisine anchor + the last 2 noun-y tokens.
        head = cuisine_anchor.split()[:1]
        tail = toks[-2:] if len(toks) >= 2 else toks
        merged: list[str] = []
        for t in head + tail:
            if t and t.lower() not in {x.lower() for x in merged}:
                merged.append(t)
        return " ".join(merged).strip()

    async def _fetch(query: str, is_hero: bool) -> str:
        from app.services.unsplash import search_photos
        async with sem:
            try:
                photos = await search_photos(query, count=_CANDIDATE_POOL, orientation="landscape")
            except Exception as exc:
                logger.debug("bind_landing_images: search '%s' failed: %s", query, exc)
                photos = []
            # Retry with a simpler / broader query when the first returns nothing.
            if not photos:
                fallback_q = _simplify(query)
                if fallback_q and fallback_q.lower() != query.lower():
                    try:
                        photos = await search_photos(fallback_q, count=_CANDIDATE_POOL, orientation="landscape")
                        if photos:
                            logger.info(
                                "bind_landing_images: retry '%s' -> '%s' succeeded",
                                query, fallback_q,
                            )
                    except Exception as exc:
                        logger.debug(
                            "bind_landing_images: retry search '%s' failed: %s",
                            fallback_q, exc,
                        )
        if not photos:
            return ""
        idx = _project_offset(project_seed, query, len(photos))
        chosen = photos[idx]
        return chosen["url_hero"] if is_hero else chosen["url_card"]

    fetched = await asyncio.gather(
        *(_fetch(q, hero) for _, _, q, hero in jobs),
        return_exceptions=False,
    )

    bound = 0
    for (s_idx, i_idx, query, _), url in zip(jobs, fetched):
        if not url:
            continue
        try:
            sections[s_idx]["images"][i_idx]["url"] = url
            sections[s_idx]["images"][i_idx].setdefault("alt", query)
            bound += 1
        except (KeyError, IndexError):
            continue

    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(content, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("bind_landing_images: write failed (%s)", exc)
        return {"requested": len(jobs), "bound": 0}

    logger.info("bind_landing_images: bound %d/%d images", bound, len(jobs))
    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ Bound {bound}/{len(jobs)} images",
            })
        except Exception:
            pass

    return {"requested": len(jobs), "bound": bound}
