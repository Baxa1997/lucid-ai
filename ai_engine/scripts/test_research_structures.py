"""End-to-end research test for landing briefs + website plans.

Runs the upgraded landing-brief research (3 Gemini calls each: structure
research grounded, design DNA grounded, structured distill) on 6 landing
prompts in parallel, and the website-plan research (1 Gemini call each)
on 6 multi-page prompts in parallel.

Output: ``ai_engine/scripts/_out/research_structures/`` with:
  • landing_<slug>.json — full brief JSON per prompt
  • website_<slug>.json — full plan JSON per prompt
  • summary.md          — section-by-section overview, conversion-element coverage

Run from ai_engine/:
    venv/bin/python scripts/test_research_structures.py
    venv/bin/python scripts/test_research_structures.py --only landing
    venv/bin/python scripts/test_research_structures.py --only website
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import sys
import time
from pathlib import Path

print = functools.partial(print, flush=True)
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Load .env from repo root ──────────────────────────────────────────────
def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# These are required by app.config but unused in this script.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "dummy"))


# ── Test cases ────────────────────────────────────────────────────────────
# Six landing prompts spanning the conversion archetypes the upgraded
# research is supposed to handle: bookable, e-commerce, SaaS, hospitality,
# wellness, services. Each should produce a landing brief with the right
# conversion stack (hero filter, trust bar, mid CTA, lead form, FAQ,
# sticky CTA).
LANDING_CASES = {
    "tours_world":          ("Landing page for touristic tours around the world", "hospitality_travel"),
    "boutique_hotel":       ("Landing page for a boutique hotel in Santorini called Pelagia Suites", "hospitality_travel"),
    "yoga_studio":          ("Landing page for a yoga and breathwork studio in Brooklyn called Common Ground", "fitness_wellness"),
    "dental_practice":      ("Landing page for a modern dental practice in Austin called Bloom Dental that offers cleanings, whitening, and Invisalign", "healthcare"),
    "saas_invoicing":       ("Landing page for a B2B invoicing SaaS for freelancers called Levin — connect bank, send invoices, accept Stripe", "saas"),
    "italian_restaurant":   ("Landing page for a Northern Italian trattoria in the West Village called Da Marco", "food_and_beverage"),
}

# Broader 10-case set spanning conversion-heavy AND non-conversion domains.
# Used by ``--ten`` to verify the dynamic system correctly OMITS elements
# when references don't use them (portfolio, gallery brand → minimal
# conversion stack) while still HITTING them on transactional domains.
LANDING_CASES_TEN = {
    # ── conversion-heavy ───────────────────────────────────────────
    "language_app":         ("Landing page for an online language-learning app called Lingua that teaches conversational Spanish via 10-min daily lessons — free 7-day trial", "saas"),
    "local_plumber":        ("Landing page for a 24/7 emergency plumber in Phoenix called Desert Plumbing — fast service, transparent pricing, free quotes", "services_local"),
    "mountain_lodge":       ("Landing page for an eco-luxury mountain lodge in the Colorado Rockies called Timber & Pine — chalets, guided hikes, farm-to-table dining", "hospitality_travel"),
    "cbd_brand":            ("Landing page for a premium CBD wellness e-commerce brand called Stillwater — tinctures, salves, sleep gummies, subscribe & save", "retail_fashion"),
    "pediatric_clinic":     ("Landing page for a modern pediatric clinic in Seattle called Little Roots — well-child visits, vaccinations, lactation support, telehealth", "healthcare"),
    "crypto_exchange":      ("Landing page for a regulated crypto trading platform called Vault — spot trading, staking, institutional custody, sign up in 60 seconds", "saas"),
    "vegan_meals":          ("Landing page for a chef-prepared vegan meal delivery service called Verdant — weekly menu, no commitment, deliver to NYC and LA", "food_and_beverage"),
    "injury_law":           ("Landing page for a personal injury law firm in Houston called Hartwell Law — car accidents, no fee unless you win, free case review", "services_local"),

    # ── NON-conversion (test that the system correctly skips conversion stack) ──
    "photo_portfolio":      ("Landing page for a wedding and portrait photographer in Brooklyn called Ana Reyes Photography — portfolio, about, contact", "creative_arts"),
    "indie_game":           ("Landing page for an indie atmospheric puzzle game called Glasshouse — trailer, screenshots, Steam wishlist, dev blog", "creative_arts"),
}

# Broader 10-case set spanning multi-page archetypes — used by
# ``--ten-web`` to verify the upgraded conversion-research pipeline
# applies the right per-page stack across diverse domains.
WEBSITE_CASES_TEN = {
    # ── conversion-heavy / transactional ───────────────────────────
    "tour_operator": (
        "a website for a small-group adventure tour operator called Nomad Trails — "
        "tours, destinations, dates, custom trips, traveler reviews, FAQ, booking",
        {"business_category": "hospitality_travel", "business_subcategory": "tour_operator"},
    ),
    "dental_clinic": (
        "a website for a modern dental practice in Austin called Bloom Dental — services, "
        "team, before/after gallery, insurance, book online, locations",
        {"business_category": "healthcare", "business_subcategory": "dental"},
    ),
    "real_estate_brokerage": (
        "a website for a luxury real estate team in Miami called Coast & Co — listings, "
        "neighborhoods, agents, sell with us, buying guides, contact",
        {"business_category": "real_estate", "business_subcategory": "luxury_residential"},
    ),
    "fitness_studio": (
        "a website for a boutique HIIT and strength fitness studio called Forge — class types, "
        "schedule, trainers, memberships, transformations, locations",
        {"business_category": "fitness_wellness", "business_subcategory": "boutique_studio"},
    ),
    "law_firm_immigration": (
        "a website for a boutique immigration law firm in Houston called Carrillo Law — "
        "visa types, attorneys, success stories, free consultation, blog-free site",
        {"business_category": "services_local", "business_subcategory": "law"},
    ),
    "saas_analytics": (
        "a website for a product analytics SaaS called Quanta — product, pricing, "
        "customers, integrations, security, careers, free trial",
        {"business_category": "saas", "business_subcategory": "analytics"},
    ),
    "ecommerce_skincare": (
        "a website for a clean-beauty skincare brand called Lumen — shop, ingredients, "
        "routines quiz, journal, sustainability, stores",
        {"business_category": "retail_fashion", "business_subcategory": "skincare"},
    ),
    "restaurant_group": (
        "a website for a 3-location modern Italian restaurant group called Salvio — menus, "
        "locations, private events, chefs, press, reservations",
        {"business_category": "food_and_beverage", "business_subcategory": "fine_dining"},
    ),

    # ── lighter / non-conversion (verifies the system correctly skips conversion stack) ──
    "architecture_studio": (
        "a portfolio site for a minimalist architecture studio called North/Field that "
        "designs cabins and small homes in the Pacific Northwest",
        {"business_category": "creative_arts", "business_subcategory": "architecture"},
    ),
    "photography_agency": (
        "a website for a high-end editorial photography agency representing 12 photographers "
        "called Field & Frame — roster, work, agents, contact",
        {"business_category": "creative_arts", "business_subcategory": "agency"},
    ),
}

# Six multi-page website prompts spanning marketplace, portfolio,
# consumer brand, services, real estate, SaaS — each archetype maps to
# a different page-count range inside _compute_page_range.
WEBSITE_CASES = {
    "vintage_camera_market": (
        "a peer-to-peer marketplace for vintage analog cameras called Shutter — "
        "buyers browse listings by format, sellers list their gear with photos and condition notes",
        {"business_category": "marketplace", "business_subcategory": "vintage_marketplace"},
    ),
    "architecture_studio": (
        "a portfolio site for a minimalist architecture studio called North/Field "
        "that designs cabins and small homes in the Pacific Northwest",
        {"business_category": "creative_arts", "business_subcategory": "architecture"},
    ),
    "fashion_brand": (
        "a website for a sustainable men's fashion brand called Hartline — shop, lookbook, "
        "story, sustainability practices, retail stores",
        {"business_category": "retail_fashion", "business_subcategory": "menswear"},
    ),
    "law_firm": (
        "a website for a boutique immigration law firm in Houston called Carrillo Law — "
        "visa types, attorneys, success stories, consultations",
        {"business_category": "services_local", "business_subcategory": "law"},
    ),
    "real_estate_team": (
        "a website for a luxury real estate team in Miami called Coast & Co — listings, "
        "neighborhoods, agents, selling guides, contact",
        {"business_category": "real_estate", "business_subcategory": "luxury_residential"},
    ),
    "data_platform_saas": (
        "a website for an analytics SaaS called Quanta — product, pricing, customer logos, "
        "integrations, docs landing, security, careers",
        {"business_category": "saas", "business_subcategory": "analytics"},
    ),
}


# ── Capture WS events silently ────────────────────────────────────────────
class CaptureWS:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.events.append(payload)


# ── Output dir ────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).resolve().parent / "_out" / "research_structures"


def _ensure_out_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Landing runner ────────────────────────────────────────────────────────
async def _run_one_landing(slug: str, prompt: str, domain: str) -> dict:
    from app.services.landing_brief import build_landing_brief

    t0 = time.monotonic()
    ws = CaptureWS()
    try:
        brief = await build_landing_brief(
            prompt,
            classification={"domain": domain},
            websocket=ws,
            timeout_s=240.0,
        )
        elapsed = time.monotonic() - t0
        return {"slug": slug, "prompt": prompt, "brief": brief, "elapsed_s": elapsed, "error": None}
    except Exception as exc:
        import traceback
        elapsed = time.monotonic() - t0
        return {
            "slug": slug, "prompt": prompt, "brief": None, "elapsed_s": elapsed,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


async def run_landings(cases: dict[str, tuple[str, str]] | None = None) -> list[dict]:
    cases = cases if cases is not None else LANDING_CASES
    print(f"\n=== Running {len(cases)} landing-brief tests in parallel ===")
    results = await asyncio.gather(*(
        _run_one_landing(slug, prompt, domain)
        for slug, (prompt, domain) in cases.items()
    ))
    for r in results:
        status = "ERR" if r["error"] else "OK"
        n_sec = len(r["brief"].get("sections", [])) if r["brief"] else 0
        n_feat = len(r["brief"].get("page_features", [])) if r["brief"] else 0
        print(f"  [{status}] {r['slug']:24s} {r['elapsed_s']:6.1f}s  sections={n_sec}  page_features={n_feat}")
        if r["error"]:
            print(f"        error: {r['error'][:200]}")
    return results


# ── Website runner ────────────────────────────────────────────────────────
async def _run_one_website(slug: str, prompt: str, intent_seed: dict) -> dict:
    from app.services.website_plan import build_website_plan

    intent = {
        "business_category":      intent_seed.get("business_category", "general"),
        "business_subcategory":   intent_seed.get("business_subcategory", "general"),
        "geographic_specifics":   intent_seed.get("geographic_specifics", "United States"),
        "target_audience":        {"primary": "general consumers"},
        "brand_personality":      ["modern", "warm", "trustworthy"],
        "tone":                   "confident",
    }
    visual_dna = {
        "cultural_intensity":  "balanced",
        "layout_signature":    "modern responsive grid",
        "section_anatomies":   {},
    }

    t0 = time.monotonic()
    try:
        plan = await build_website_plan(prompt, intent, visual_dna, timeout_s=90.0)
        elapsed = time.monotonic() - t0
        return {"slug": slug, "prompt": prompt, "plan": plan, "elapsed_s": elapsed, "error": None}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return {"slug": slug, "prompt": prompt, "plan": None, "elapsed_s": elapsed, "error": str(exc)}


async def run_websites(cases: dict[str, tuple[str, dict]] | None = None) -> list[dict]:
    cases = cases if cases is not None else WEBSITE_CASES
    print(f"\n=== Running {len(cases)} website-plan tests in parallel ===")
    results = await asyncio.gather(*(
        _run_one_website(slug, prompt, intent_seed)
        for slug, (prompt, intent_seed) in cases.items()
    ))
    for r in results:
        status = "ERR" if r["error"] else "OK"
        n_pages = len(r["plan"].get("pages", [])) if r["plan"] else 0
        print(f"  [{status}] {r['slug']:24s} {r['elapsed_s']:6.1f}s  pages={n_pages}")
        if r["error"]:
            print(f"        error: {r['error'][:200]}")
    return results


# ── Conversion-element detector ───────────────────────────────────────────
# Maps each conversion-completeness element to the section types / page
# features that signal its presence. Used to score the upgraded research.
CONVERSION_ELEMENTS = {
    "sticky_cta":       (None, {"sticky_cta"}),
    "hero_filter":      (None, None),  # detected by inspecting hero.interactivity
    "trust_bar":        ({"trust_bar"}, None),
    "mid_page_cta":     ({"mid_cta_banner"}, None),
    "lead_form":        ({"lead_form", "quote_form", "contact_form"}, None),
    "faq":              ({"faq"}, None),
    "social_proof":     ({"testimonials", "stats", "press"}, None),
}


def _detect_hero_filter(brief: dict) -> bool:
    """Look for a search/filter widget on the hero."""
    for s in brief.get("sections", []):
        if (s.get("type") or "").lower() != "hero":
            continue
        interact = (s.get("interactivity") or "").lower()
        if any(kw in interact for kw in ("filter", "search", "select", "dropdown", "calendar", "date picker")):
            return True
    return False


def _coverage_row(brief: dict) -> dict[str, bool]:
    types = {(s.get("type") or "").lower() for s in brief.get("sections", [])}
    features = set(brief.get("page_features") or [])
    out = {}
    for key, (sect_types, feat_set) in CONVERSION_ELEMENTS.items():
        if key == "hero_filter":
            out[key] = _detect_hero_filter(brief)
            continue
        hit = False
        if sect_types and types & sect_types:
            hit = True
        if feat_set and features & feat_set:
            hit = True
        out[key] = hit
    return out


# ── Persistence + summary ─────────────────────────────────────────────────
def _dump_results(landing: list[dict], website: list[dict]) -> None:
    _ensure_out_dir()
    for r in landing:
        path = OUT_DIR / f"landing_{r['slug']}.json"
        path.write_text(json.dumps(r["brief"] or {"error": r["error"]}, indent=2))
    for r in website:
        path = OUT_DIR / f"website_{r['slug']}.json"
        path.write_text(json.dumps(r["plan"] or {"error": r["error"]}, indent=2))
    print(f"\n✓ Wrote {len(landing) + len(website)} JSON files to {OUT_DIR}")


def _write_summary(landing: list[dict], website: list[dict]) -> None:
    lines: list[str] = []
    lines.append("# Research test — structure dump")
    lines.append("")
    lines.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')}_")
    lines.append("")

    # Landing summaries ────────────────────────────────────────────────────
    lines.append("## Landing briefs")
    lines.append("")
    lines.append("### Section counts + conversion coverage")
    lines.append("")
    lines.append("| Slug | Sections | Page features | sticky_cta | hero_filter | trust_bar | mid_cta | lead_form | faq | social_proof |")
    lines.append("|------|---------:|---------------|:----------:|:-----------:|:---------:|:-------:|:---------:|:---:|:------------:|")
    for r in landing:
        if not r["brief"]:
            lines.append(f"| {r['slug']} | ERR | — | — | — | — | — | — | — | — |")
            continue
        b = r["brief"]
        cov = _coverage_row(b)
        n_sec = len(b.get("sections", []))
        feats = ",".join(b.get("page_features") or []) or "—"
        cells = " | ".join("✅" if cov[k] else "❌" for k in ("sticky_cta", "hero_filter", "trust_bar", "mid_page_cta", "lead_form", "faq", "social_proof"))
        lines.append(f"| {r['slug']} | {n_sec} | {feats} | {cells} |")
    lines.append("")
    lines.append("### Section orders")
    lines.append("")
    for r in landing:
        lines.append(f"**{r['slug']}** — _{r['prompt']}_")
        if not r["brief"]:
            lines.append(f"- ERROR: {r['error']}")
            lines.append("")
            continue
        b = r["brief"]
        order = " → ".join((s.get("type") or "?") for s in b.get("sections", []))
        lines.append(f"- Brand: **{(b.get('brand') or {}).get('name', '?')}** — {(b.get('brand') or {}).get('tagline', '')}")
        lines.append(f"- Sections ({len(b.get('sections', []))}): `{order}`")
        if b.get("page_features"):
            lines.append(f"- Page features: `{', '.join(b['page_features'])}`")
        lines.append(f"- Header / Footer: `{b.get('header_archetype', '?')}` / `{b.get('footer_archetype', '?')}`")
        lines.append("")

    # Website summaries ───────────────────────────────────────────────────
    lines.append("## Website plans")
    lines.append("")
    lines.append("### Per-site conversion-element distribution (counts across all pages)")
    lines.append("")
    lines.append("| Slug | Pages | trust_bar | mid_cta | lead_form* | faq | testimonials |")
    lines.append("|------|------:|:---------:|:-------:|:----------:|:---:|:------------:|")
    _LEAD_FORMS = {"lead_form", "quote_form", "booking_form", "contact_form", "reservation"}
    for r in website:
        if not r["plan"]:
            lines.append(f"| {r['slug']} | ERR | — | — | — | — | — |")
            continue
        p = r["plan"]
        all_types: list[str] = []
        for page in p.get("pages", []):
            for s in (page.get("sections") or []):
                all_types.append((s.get("type") or "").lower())
        def _hits(types: set[str]) -> int:
            return sum(1 for t in all_types if t in types)
        n_tb = _hits({"trust_bar"})
        n_mc = _hits({"mid_cta_banner"})
        n_lf = _hits(_LEAD_FORMS)
        n_faq = _hits({"faq"})
        n_test = _hits({"testimonials"})
        lines.append(f"| {r['slug']} | {len(p.get('pages', []))} | {n_tb} | {n_mc} | {n_lf} | {n_faq} | {n_test} |")
    lines.append("")
    lines.append("\\* lead_form column counts any conversion-form section type (lead_form, quote_form, booking_form, contact_form, reservation).")
    lines.append("")

    lines.append("### Page counts + routes")
    lines.append("")
    lines.append("| Slug | Pages | Routes |")
    lines.append("|------|------:|--------|")
    for r in website:
        if not r["plan"]:
            lines.append(f"| {r['slug']} | ERR | — |")
            continue
        p = r["plan"]
        routes = ", ".join(page.get("route", "?") for page in p.get("pages", []))
        lines.append(f"| {r['slug']} | {len(p.get('pages', []))} | `{routes}` |")
    lines.append("")
    lines.append("### Per-page sections")
    lines.append("")
    for r in website:
        lines.append(f"**{r['slug']}** — _{r['prompt']}_")
        if not r["plan"]:
            lines.append(f"- ERROR: {r['error']}")
            lines.append("")
            continue
        p = r["plan"]
        for page in p.get("pages", []):
            order = " → ".join((s.get("type") or "?") for s in (page.get("sections") or []))
            n_sec = len(page.get("sections") or [])
            lines.append(f"- `{page.get('route', '?')}` ({n_sec}): {order}")
        lines.append("")

    summary_path = OUT_DIR / "summary.md"
    summary_path.write_text("\n".join(lines))
    print(f"✓ Wrote summary to {summary_path}")


# ── Main ──────────────────────────────────────────────────────────────────
async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("landing", "website"), default=None)
    parser.add_argument("--smoke", action="store_true", help="Run 1 landing case only as a smoke test")
    parser.add_argument("--ten", action="store_true", help="Use the broader 10-case landing set (mix of conversion + non-conversion)")
    parser.add_argument("--ten-web", action="store_true", help="Use the broader 10-case multi-page website set")
    args = parser.parse_args()

    if args.smoke:
        # Single-case smoke test before launching all 12.
        slug, (prompt, domain) = next(iter(LANDING_CASES.items()))
        print(f"=== SMOKE TEST: {slug} ===")
        result = await _run_one_landing(slug, prompt, domain)
        print(f"Result: {'OK' if not result['error'] else 'ERR'}  {result['elapsed_s']:.1f}s")
        if result["error"]:
            print(f"Error: {result['error']}")
            print(result.get("traceback") or "")
            return 1
        b = result["brief"]
        print(f"Brand: {(b.get('brand') or {}).get('name')}")
        print(f"Sections ({len(b.get('sections', []))}):")
        for s in b.get("sections", []):
            print(f"  - {s.get('type', '?'):20s} arch={s.get('archetype', '-')} interact={(s.get('interactivity') or '')[:60]}")
        print(f"Page features: {b.get('page_features', [])}")
        print(f"Header/Footer: {b.get('header_archetype')} / {b.get('footer_archetype')}")
        _ensure_out_dir()
        (OUT_DIR / f"landing_{slug}.json").write_text(json.dumps(b, indent=2))
        return 0

    landing_results: list[dict] = []
    website_results: list[dict] = []

    landing_cases = LANDING_CASES_TEN if args.ten else LANDING_CASES
    website_cases = WEBSITE_CASES_TEN if args.ten_web else WEBSITE_CASES

    # When --ten or --ten-web is set, run only the matching half unless
    # --only also asks for the other half explicitly. This keeps single-
    # flag invocations focused on the 10-case set they target.
    run_landings_flag = args.only in (None, "landing")
    run_websites_flag = args.only in (None, "website")
    if args.ten and args.only is None:
        run_websites_flag = False
    if args.ten_web and args.only is None:
        run_landings_flag = False

    if run_landings_flag:
        landing_results = await run_landings(landing_cases)
    if run_websites_flag:
        website_results = await run_websites(website_cases)

    _dump_results(landing_results, website_results)
    _write_summary(landing_results, website_results)

    n_landing_err = sum(1 for r in landing_results if r["error"])
    n_website_err = sum(1 for r in website_results if r["error"])
    print(f"\n=== DONE — landing OK: {len(landing_results) - n_landing_err}/{len(landing_results)}  website OK: {len(website_results) - n_website_err}/{len(website_results)} ===")
    return 1 if (n_landing_err or n_website_err) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
