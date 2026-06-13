"""Deterministic page-rhythm composer — the "art director" decision step.

Sections generate IN PARALLEL, so without a page-level plan each codegen call
picks its own background/composition and the page reads as N random
components. This module makes the global decisions BEFORE codegen, in code:

  research (surface_rhythm, flagship_sections — optional fields on
  visual_dna) + the brief's section list + a project-stable seed
      → one assignment per section: surface, density, flagship,
        variety hint, and the neighbors' surfaces.

Design rules enforced mechanically (planner/executor split — executors never
make global choices):
  • hero owns its media treatment — the plan only fixes its neighbors.
  • final conversion section (cta/newsletter) sits on the primary band.
  • light/alternating modes: no two adjacent sections share a surface, and
    dark inverse bands are budgeted (≈1 per 4 sections, never adjacent to
    another band).
  • dark-editorial mode: inverse is the canvas; one light "breather" mid-page,
    flagship + conversion sections punctuate it.
  • flagship sections (research-chosen showcases) get the inverse band in
    light modes when budget allows, and always get "airy" density.

Pure functions, seeded determinism: same project → same plan (regeneration
stability), different projects → different plans.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Surface vocabulary → the exact Tailwind classes codegen must apply to the
# section root. Text color is part of the surface so inverse bands can't
# ship invisible text.
SURFACES: dict[str, str] = {
    "base":    "bg-background text-foreground",
    "tint":    "bg-muted/40 text-foreground",
    "inverse": "bg-foreground text-background",
    "band":    "bg-primary text-primary-foreground",
    "media":   "(hero media treatment — full-bleed photo/gradient per hero pattern)",
}

_CONVERSION_TYPES = {"cta", "cta_band", "cta_final", "newsletter", "mid_cta_banner"}
_FORM_TYPES = {"contact", "contact_form", "reservation", "booking_form", "reservations", "booking"}
_RHYTHM_MODES = {"light", "alternating", "dark-editorial"}


def _seed_int(seed: str, salt: str) -> int:
    h = hashlib.md5(f"{seed}::{salt}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _stype(section: dict[str, Any]) -> str:
    return (section.get("type") or section.get("role") or "").strip().lower()


def _normalize_mode(visual_dna: dict[str, Any] | None) -> str:
    raw = ((visual_dna or {}).get("surface_rhythm") or "").strip().lower()
    if raw in _RHYTHM_MODES:
        return raw
    # Heuristic default when research omitted the field: dark-editorial is
    # opt-in only; everything else alternates.
    return "alternating"


def _flagship_types(visual_dna: dict[str, Any] | None, sections: list[dict[str, Any]]) -> set[str]:
    wanted = [
        (s or "").strip().lower()
        for s in ((visual_dna or {}).get("flagship_sections") or [])
        if (s or "").strip()
    ]
    present = {_stype(s) for s in sections}
    return {w for w in wanted if w in present}


def compose_page_rhythm(
    sections: list[dict[str, Any]],
    visual_dna: dict[str, Any] | None = None,
    *,
    seed: str = "",
) -> dict[str, dict[str, Any]]:
    """Assign a surface/density plan per section id.

    Returns {section_id: {surface, surface_classes, density, flagship,
    variety_hint, prev_surface, next_surface, mode}}. Sections without an id
    are skipped (codegen requires ids upstream).
    """
    mode = _normalize_mode(visual_dna)
    flagships = _flagship_types(visual_dna, sections)

    ordered = [s for s in sections if (s.get("id") or "").strip()]
    n = len(ordered)
    if n == 0:
        return {}

    surfaces: list[str] = [""] * n

    # ── pass 1: fixed roles ─────────────────────────────────────────────
    # The brief mandates the first section is the hero; treat index 0 as
    # media regardless so a mistyped hero never derails the whole plan.
    for i, s in enumerate(ordered):
        st = _stype(s)
        if i == 0 or "hero" in st:
            surfaces[i] = "media"
        elif st in _CONVERSION_TYPES:
            surfaces[i] = "band"

    # ── pass 2: mode canvas ─────────────────────────────────────────────
    if mode == "dark-editorial":
        # Inverse canvas; one light breather near the middle of the page.
        breather_idx = None
        candidates = [
            i for i, s in enumerate(ordered)
            if not surfaces[i] and _stype(s) not in _FORM_TYPES
        ]
        if candidates:
            breather_idx = candidates[len(candidates) // 2]
        for i in range(n):
            if surfaces[i]:
                continue
            surfaces[i] = "base" if i == breather_idx else "inverse"
    else:
        # Light canvas: alternate base/tint; sprinkle a budgeted number of
        # inverse bands ("alternating" mode only), flagships first in line.
        unassigned = [i for i in range(n) if not surfaces[i]]
        inverse_budget = 0
        if mode == "alternating":
            inverse_budget = max(1, len(unassigned) // 4)

        inverse_picks: list[int] = []
        if inverse_budget:
            # Flagship sections claim the dark showcase bands first.
            for i in unassigned:
                if len(inverse_picks) >= inverse_budget:
                    break
                if _stype(ordered[i]) in flagships:
                    inverse_picks.append(i)
            # Remaining budget lands on a seeded mid-page pick (never the
            # section right after the hero, never a form).
            if len(inverse_picks) < inverse_budget:
                pool = [
                    i for i in unassigned
                    if i not in inverse_picks
                    and i >= 2
                    and _stype(ordered[i]) not in _FORM_TYPES
                ]
                if pool:
                    pick = pool[_seed_int(seed, f"inverse{len(inverse_picks)}") % len(pool)]
                    inverse_picks.append(pick)

        for i in inverse_picks:
            # A band adjacent to another band/inverse reads as one giant
            # dark slab — skip the pick instead.
            prev_s = surfaces[i - 1] if i > 0 else ""
            next_s = surfaces[i + 1] if i < n - 1 else ""
            if prev_s in ("inverse", "band") or next_s in ("inverse", "band"):
                continue
            surfaces[i] = "inverse"

        # Alternate the rest against the left neighbor (tint only ever
        # follows base, so two identical light surfaces can't touch; any
        # slot after media/inverse/band restarts on base).
        for i in range(n):
            if surfaces[i]:
                continue
            prev_s = surfaces[i - 1] if i > 0 else "media"
            surfaces[i] = "tint" if prev_s == "base" else "base"

    # ── pass 3: assemble assignments ────────────────────────────────────
    plan: dict[str, dict[str, Any]] = {}
    for i, s in enumerate(ordered):
        sid = (s.get("id") or "").strip()
        st = _stype(s)
        flagship = st in flagships
        plan[sid] = {
            "mode": mode,
            "surface": surfaces[i],
            "surface_classes": SURFACES[surfaces[i]],
            "density": "airy" if flagship else "regular",
            "flagship": flagship,
            "variety_hint": _seed_int(seed, sid) % 4,
            "prev_surface": surfaces[i - 1] if i > 0 else "",
            "next_surface": surfaces[i + 1] if i < n - 1 else "",
        }
    return plan


def rhythm_prompt_block(assignment: dict[str, Any] | None) -> str:
    """Render one section's assignment as the ART DIRECTION prompt block."""
    if not assignment:
        return ""
    surface = assignment.get("surface") or ""
    lines = ["", "ART DIRECTION — PAGE-LEVEL PLAN (authoritative; decided before codegen):"]
    if surface == "media":
        lines.append(
            "  Surface: the hero owns its media treatment (full-bleed photo / gradient per "
            "the hero pattern). The plan does not override it."
        )
    else:
        lines.append(
            f"  Surface: apply `{assignment.get('surface_classes', '')}` on the section ROOT. "
            "Do NOT choose a different background — every section's surface was planned "
            "together so the page reads as one designed arc."
        )
    prev_s, next_s = assignment.get("prev_surface"), assignment.get("next_surface")
    if prev_s or next_s:
        lines.append(
            f"  Neighbors: previous section sits on `{prev_s or '(page start)'}`, next on "
            f"`{next_s or '(page end)'}`. Your surface already contrasts with both — do not "
            "add your own full-bleed background that matches a neighbor."
        )
    if assignment.get("surface") == "inverse":
        lines.append(
            "  Inverse surface: body text `text-background` / muted `text-background/70` — "
            "NEVER text-foreground or text-muted-foreground on this section."
        )
    if assignment.get("flagship"):
        lines.append(
            "  FLAGSHIP section: this is one of the page's 1-2 showcases — largest scale, "
            "deepest decorative investment, most generous whitespace (density: airy)."
        )
    else:
        lines.append(f"  Density: {assignment.get('density', 'regular')}.")
    lines.append(
        f"  Variety: when this section type's pattern catalog offers options A-E, prefer "
        f"viable option #{int(assignment.get('variety_hint', 0)) + 1} for this brand "
        "(deterministic variety — never default to the first pattern)."
    )
    lines.append(
        "  Precedence: if the ANATOMY text above implies a different background/surface, "
        "THIS plan wins on surface — keep the anatomy's composition, swap its surface."
    )
    return "\n".join(lines) + "\n"
