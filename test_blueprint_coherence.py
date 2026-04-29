"""A/B test: does adding archetype-bundle examples to LAYOUT_BLUEPRINT improve coherence?

Calls Gemini twice per domain (variant A = current free-form; variant B = current +
6 archetype examples as coherence references). Saves outputs to test_results/ for diff.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
RESULTS = ROOT / "test_results_blueprint"
RESULTS.mkdir(exist_ok=True)


def load_env():
    if not ENV_PATH.exists():
        sys.exit(f"missing {ENV_PATH}")
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
API_KEY = os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    sys.exit("GOOGLE_API_KEY not set")

# Use flash for speed/cost — coherence question doesn't need pro
MODEL = "gemini-2.5-flash"
URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL}:generateContent?key={API_KEY}"
)


# ── Variant A: current prompt structure (trimmed to LAYOUT_BLUEPRINT only) ──
BLUEPRINT_FIELDS = """===LAYOUT_BLUEPRINT===

hero_archetype:
  [ONE of: split / bento / diagonal / magazine / layered-scroll / cinematic-parallax /
   editorial-offset / full-bleed-dark / product-showcase / typographic-hero / INVENT one]
  reasoning: [1 sentence why this fits the domain mood]

features_archetype:
  [ONE of: bento-mixed / zigzag / vertical-tabs / horizontal-scroll / masonry /
   tilt-stack / showcase / timeline / numbered-editorial / INVENT.]

card_language:
  [1-2 sentences: radius style, border, shadow/glow, micro-element.]

typography_pairing:
  [Heading font + body font (real Google Fonts, different from each other). Weight/tracking/italic rules.]

motion_language:
  [2-3 complementary behaviors. MUST specify: enter animation, hover state, scroll-linked behavior, emotional register.]

decorative_pattern:
  [ONE pattern/texture across multiple sections at low opacity. Include how it's applied in Tailwind/CSS.]

border_radius_language:
  [Philosophy + EXACT Tailwind values for: buttons, cards, images, inputs.]

color_application_strategy:
  [ONE of: mono-accent / duotone-photos / gradient-mesh / inverted-dark / polychrome /
   photographic-neutral / brand-flood. Specify which sections get which treatment.]

hover_interaction_style:
  [1-2 consistent behaviors for: primary cards, CTAs, image cards, nav links.]

spacing_rhythm:
  [ONE of: tight-editorial / standard-modern / airy-luxury / asymmetric / dense-information.]

live_ui_recipe:
  [4-6 sentences: ONE signature motion moment + supporting micro-interactions. Domain-specific.]

scroll_reveal_style:
  [Exact framer-motion values: y-distance, duration, easing, stagger interval, viewport amount trigger.]

ambient_motion:
  [gradient-mesh / floating-orbs / grain-noise / subtle-scan / none. CSS keyframe + JSX placement.]

design_dna_summary:
  [ONE sentence, 15-25 words: the unique design recipe.]
"""

VARIANT_A_HEAD = """You are an AWARD-WINNING VISUAL DESIGN DIRECTOR.
Output the LAYOUT_BLUEPRINT for the project below. Every field needs concrete, code-translatable specifics.
No generic phrases like "modern clean design".

PROJECT: "{description}"
DOMAIN: {domain}

"""

# ── Variant B: same prompt + archetype bundle examples ──
ARCHETYPE_EXAMPLES = """
════════════════════════════════════════════════════════════
STYLISTIC COHERENCE — REFERENCE BUNDLES (read before deciding)
════════════════════════════════════════════════════════════
Below are 6 internally-coordinated style systems. Notice how within each bundle, motion
+ texture + typography + cards + section rhythm all HARMONIZE — they belong together.

Your job is NOT to copy one of these. Your job is to produce YOUR OWN bundle where
every variable harmonizes the same way these do. A bundle where headlines feel luxury
but motion feels electric brutalist is a FAILURE.

BUNDLE 1 — OBSIDIAN CINEMATIC (dark, premium tech)
  • bg: deep near-black (#0a0d12) · texture: 80px grid + SVG noise 0.035
  • headline: Syne / Bebas weight-800 clamp(56px,8vw,110px), tight tracking
  • motion: fadeUp 0.5s stagger 0.08s · glow pulses · scroll-line indicator
  • cards: border border-white/7 bg-surface · subtle inner glow
  • buttons: solid accent, glow-shadow, font-semibold
  • section rhythm: hero → ticker → bento features → how-it-works → pricing → testimonials → FAQ → CTA
  • ambient: gradient orbs + grain layered

BUNDLE 2 — EDITORIAL LIGHT (publications, magazine, agency)
  • bg: warm off-white (#fafaf8) · texture: 24px dot grid radial-gradient 1px circles
  • headline: Playfair Display italic clamp(48px,6vw,96px) · accent serif
  • motion: revealWipe clip-path 0.7s · slow parallax · image reveal
  • cards: bg-white shadow-sm rounded-sm · sharp corners
  • buttons: border-2 border-accent text-accent rounded-none
  • section rhythm: hero → featured article → category grid → newsletter → trending → author picks
  • ambient: dot grid only · no orbs

BUNDLE 3 — LUXURY DARK (fashion, hospitality, fine dining)
  • bg: full-bleed photo with dark scrim · texture: repeating diagonal gold lines opacity 0.03
  • headline: Cormorant Garamond italic, bottom-left placement, letter-spacing wide
  • motion: ultra-slow 0.8s-1.2s opacity-only fade (NO translate) · gold shimmer on CTA
  • cards: border-t border-gold/20 only · no shadow · airy
  • buttons: border border-gold/40 text-gold uppercase tracking-wider
  • section rhythm: hero → brand story → product showcase → philosophy → testimonials → contact CTA
  • ambient: none — let the photography breathe

BUNDLE 4 — ELECTRIC BOLD (sports, energy drinks, gaming)
  • bg: pure black (#0f0f0f) · texture: 40px grid + accent blob blur-[120px]
  • headline: Bebas Neue font-black clamp(72px,10vw,140px) · accent bleeds off-edge
  • motion: slideIn 0.15-0.2s SNAPPY · skewX(-2deg) on hover · high contrast flips
  • cards: border border-accent/20 · sharp angles · skew on hover
  • buttons: bg-accent text-black font-black skewX(-2deg)
  • section rhythm: hero → stats row → features scroll → showcase → community → diagonal CTA
  • ambient: floating orbs in accent color

BUNDLE 5 — WARM PROFESSIONAL (saas, productivity, fintech consumer)
  • bg: cream (#fffef7) · texture: SVG grain 0.02 + warm radial gradient
  • headline: Plus Jakarta Sans font-bold clamp(40px,5vw,72px) · split layout image right
  • motion: fadeUp 0.5s stagger 0.1s · scale-in cards · hover lift -2px
  • cards: bg-white rounded-2xl shadow-sm · soft elevation
  • buttons: bg-accent text-white rounded-xl shadow-md
  • section rhythm: hero → trust badges → features 3-col → how it works → pricing → testimonials → FAQ
  • ambient: warm radial gradient hero only

BUNDLE 6 — SOFT MINIMAL (wellness, organic, lifestyle)
  • bg: warm off-white (#fdfcfb) · texture: organic blob clip-paths + subtle SVG noise
  • headline: Fraunces italic clamp(40px,5vw,80px) · centered
  • motion: floatIn 0.9s ease · gentle float animations · translateY(-4px) hover
  • cards: bg-white rounded-3xl shadow-[0_4px_24px_rgba(0,0,0,0.06)] · pillowy
  • buttons: bg-accent/10 text-accent rounded-full · soft pill
  • section rhythm: hero → philosophy → features 2-col → testimonials → newsletter → footer
  • ambient: organic blob shapes only

════════════════════════════════════════════════════════════
COHERENCE CHECK BEFORE EMITTING
════════════════════════════════════════════════════════════
Before you write LAYOUT_BLUEPRINT, ask yourself:
  • Does my motion timing match my typography mood? (luxury serif ≠ 0.15s snappy motion)
  • Does my card chrome match my background mood? (pillowy rounded-3xl ≠ dark obsidian bg)
  • Does my ambient motion match my section rhythm? (orbs in editorial light = AI-cliché tell)
  • Could a senior art director look at this bundle and say "yes, every choice belongs together"?

If any answer is no → revise until everything harmonizes.
"""

VARIANT_B_HEAD = (
    """You are an AWARD-WINNING VISUAL DESIGN DIRECTOR.
Output the LAYOUT_BLUEPRINT for the project below. Every field needs concrete, code-translatable specifics.
No generic phrases like "modern clean design".

PROJECT: "{description}"
DOMAIN: {domain}
"""
    + ARCHETYPE_EXAMPLES
    + "\n"
)

DOMAINS = [
    ("Boutique wine bar in Brooklyn", "wine bar / hospitality"),
    ("AI dev tool for backend engineers", "developer tools / saas"),
    ("Yoga studio booking site", "wellness / fitness"),
]


async def call_gemini(prompt: str, label: str) -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 6000,
            "temperature": 0.4,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(URL, json=payload)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:500]}"
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return f"PARSE_ERROR: {str(data)[:500]}"


async def main():
    for desc, domain in DOMAINS:
        slug = desc.split()[0].lower().replace("/", "")
        for variant, head in [("A", VARIANT_A_HEAD), ("B", VARIANT_B_HEAD)]:
            prompt = head.format(description=desc, domain=domain) + BLUEPRINT_FIELDS
            label = f"{slug}_variant_{variant}"
            print(f"→ {label} (prompt {len(prompt)} chars)…", flush=True)
            text = await call_gemini(prompt, label)
            out = RESULTS / f"{label}.md"
            out.write_text(f"# {desc} — Variant {variant}\n\n{text}\n")
            print(f"  saved {out.name} ({len(text)} chars)")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
