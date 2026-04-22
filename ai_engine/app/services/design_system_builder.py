"""
Design System Builder — one dedicated Claude call that produces a bespoke,
validated, locked design system BEFORE code generation.

Why this exists:
  The old flow let Gemini research free-text a verbal "design blueprint"
  which then leaked inconsistently into 3-5 code-generation phases. Results
  drifted: hero used one palette, features another, cards reinvented
  radius/shadow per section. Quality was capped at "whatever Gemini typed".

  This module replaces that drift with one source of truth:
    (a) One Claude call with a senior-design-director system prompt.
    (b) Structured JSON output via tool-forcing (no free text).
    (c) Deterministic validation — contrast, type scale, grid alignment.
    (d) Retry once on validation failure with the specific violations fed back.

  The resulting design system is rendered into the same ===HEADER=== blocks
  the downstream schema builder and code-generation prompts already parse
  (CSS_VARIABLES, FONTS, DESIGN_SYSTEM_NAME, PALETTE, TYPOGRAPHY,
  LAYOUT_BLUEPRINT). No changes needed in downstream consumers — they just
  see stronger, more consistent content.

Usage:
    from app.services.design_system_builder import build_design_system

    design = await build_design_system(
        description=description,
        domain=_domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=_layout_archetype,
        api_key=api_key,
        websocket=websocket,
    )
    research = inject_design_blocks(research, design)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Optional

import httpx

logger = logging.getLogger("lucid.design_system_builder")


# ─── Claude call constants ───────────────────────────────────────────────────

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4000
_TIMEOUT = 90.0


# ─── System prompt — the taste instruction ──────────────────────────────────

_SYSTEM_PROMPT = """You are a senior design director with the taste of teams
behind Vercel, Linear, Stripe, Anthropic, Ramp, Attio, Notion, Mux, Framer.

Your one job is to design a BESPOKE design system for ONE specific project.
Not a template. Not a generic starter. A design system that feels crafted
for THIS brand in THIS domain.

HARD RULES:

1. TASTE BEARINGS
   - Colors: specific, restrained. Max 1 accent color. Background and
     foreground have WCAG AA contrast (4.5:1 minimum for body text).
     Never generic "blue 600" SaaS palettes on physical-world brands.
   - Typography: pair 2 Google Fonts with real personality contrast.
     Display + neutral sans is safest. All-sans with weight contrast also
     good. Monospace body is a statement — use sparingly. Never both
     heading and body as the same generic sans (Inter + Inter).
   - Spacing: base unit 4 or 8. Section padding at least 96px vertical on
     landing pages, 40-64px on admin/CRM/TMS.
   - Radius: pick ONE language (sharp / crisp / soft / organic) and stick
     with it. Emit exact per-element radii in radius_tokens — every
     component in the app MUST use one of those values, nothing else.

2. BRAND MARK
   - Every project gets a wordmark or monogram. Specify exact font,
     weight, tracking, and size behavior so the generated navbar/sidebar
     ALWAYS shows the brand — never a bare nav with no logo.

3. ANTI-GENERIC
   - Never output `217 91% 60%` (default Tailwind blue).
   - Never output `Inter + Inter` as the font pairing.
   - Never output `0.5rem` radius with no thought.
   - Never use symmetric 3-column icon grids as features_archetype.
   - Never use gradient-pastel hero as hero_archetype.
   - Never ship a landing page with generic "About / Features / Pricing /
     Sign In / Get Started" nav for a physical-world brand.

4. PROJECT-SPECIFIC
   - A coffee roaster's palette should feel like espresso + cream + one
     warm accent. A B2B SaaS analytics tool should feel like charcoal +
     off-white + one signal color. A wedding venue should feel like linen
     + sage + one terracotta accent. Read the domain and vibe before
     picking ANY HSL value.

5. COMMIT TO A PERSONALITY
   - Pick one of these archetypes (or blend two): editorial_serif_minimal,
     brutalist_mono, neo_swiss, dark_cinematic, soft_pastel_organic,
     tech_noir_gradient, maximalist_collage, minimal_luxe, warm_artisan,
     scandi_clean, japanese_ma, bauhaus_modern, magazine_editorial,
     sharp_corporate, playful_retro, botanical_organic, high_contrast_brutalist.

6. MUST BE INTERNALLY CONSISTENT
   - Signature motif appears 2-3x across the page.
   - Card language, motion language, and spacing rhythm all reflect the
     same personality. Card says "crisp hairline" → motion says "precise
     linear transitions" → spacing says "tight editorial" (not airy).
   - For admin/CRM/TMS: table, form, sidebar, toolbar, and empty-state
     language ALL share the same radius + border treatment + density.

7. WHEN THE PROJECT IS AN ADMIN PANEL / CRM / TMS / SAAS DASHBOARD / ECOMMERCE
   - Design for SCANNING, not scrolling. Density is compact-comfortable.
   - Tables do the heavy lifting — specify row height, border style,
     header weight, zebra/hover treatment.
   - Forms live inside drawers or dedicated routes with label position,
     input chrome, focus ring, and error treatment matching the brand.
   - Sidebar is the primary nav surface. Specify width, icon style,
     active state, section treatment.
   - Status colors are SEPARATE from brand accent — success/warning/info/
     neutral/error all defined, each with WCAG AA contrast when used on
     badges/fills.
   - Section padding is 32-64px (NOT 96px+). Cards pack dense info.

Output ONLY via the provided tool. No prose.
"""


# ─── Tool (forced JSON schema output) ───────────────────────────────────────

_DESIGN_TOOL = {
    "name": "emit_design_system",
    "description": (
        "Emit the complete, bespoke design system for the project. "
        "Every field must be specific to THIS project's domain and vibe — "
        "not copied from a template."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "design_system_name": {
                "type": "string",
                "description": "2-4 word evocative name unique to this product (e.g. 'Ember Roast', 'Harvest Table', 'Silk & Steel', 'Polar Grid'). Never generic like 'Modern App'.",
            },
            "archetype": {
                "type": "string",
                "description": "The design-direction archetype you committed to. One of the 17 listed in the system prompt, or a blend like 'warm_artisan + editorial_serif_minimal'.",
            },
            "personality": {
                "type": "string",
                "description": "3-5 adjectives capturing the visual personality (e.g. 'confident, bookish, warm, quietly premium').",
            },
            "palette": {
                "type": "object",
                "description": "HSL tokens as bare strings like '22 85% 45%' — NO hsl() wrapper, NO commas.",
                "properties": {
                    "background":        {"type": "string"},
                    "foreground":        {"type": "string"},
                    "primary":           {"type": "string"},
                    "primary_foreground": {"type": "string"},
                    "secondary":         {"type": "string"},
                    "secondary_foreground": {"type": "string"},
                    "accent":            {"type": "string"},
                    "accent_foreground": {"type": "string"},
                    "muted":             {"type": "string"},
                    "muted_foreground":  {"type": "string"},
                    "card":              {"type": "string"},
                    "card_foreground":   {"type": "string"},
                    "border":            {"type": "string"},
                    "ring":              {"type": "string"},
                    "destructive":       {"type": "string"},
                    "destructive_foreground": {"type": "string"},
                },
                "required": [
                    "background", "foreground", "primary", "primary_foreground",
                    "secondary", "secondary_foreground", "accent", "accent_foreground",
                    "muted", "muted_foreground", "card", "card_foreground",
                    "border", "ring", "destructive", "destructive_foreground",
                ],
            },
            "dark_palette": {
                "type": "object",
                "description": "Dark-mode overrides. Same shape as palette (only override the fields that change in dark mode).",
                "properties": {
                    "background":       {"type": "string"},
                    "foreground":       {"type": "string"},
                    "card":             {"type": "string"},
                    "card_foreground":  {"type": "string"},
                    "muted":            {"type": "string"},
                    "muted_foreground": {"type": "string"},
                    "border":           {"type": "string"},
                },
            },
            "radius": {
                "type": "string",
                "description": "Base radius with unit (e.g. '0.375rem', '0.75rem'). Picks ONE value — all other radii derive from this.",
            },
            "radius_tokens": {
                "type": "object",
                "description": "Per-element radii, ALL derived from the same radius language. Every component in the app MUST pick one of these values — no ad-hoc radii.",
                "properties": {
                    "button":      {"type": "string", "description": "e.g. '0.375rem', '0rem', '9999px'"},
                    "input":       {"type": "string"},
                    "card":        {"type": "string"},
                    "badge":       {"type": "string"},
                    "image":       {"type": "string"},
                    "modal":       {"type": "string"},
                    "tooltip":     {"type": "string"},
                },
                "required": ["button", "input", "card", "badge", "image", "modal"],
            },
            "brand_mark": {
                "type": "object",
                "description": "Logo / brand mark specification so the generated navbar and sidebar ALWAYS show the brand. Never a bare nav.",
                "properties": {
                    "treatment":   {"type": "string", "description": "'wordmark' | 'icon_plus_wordmark' | 'monogram' | 'icon_only'"},
                    "font":        {"type": "string", "description": "Google Fonts family used for the wordmark — usually the heading font, occasionally a distinct display face"},
                    "weight":      {"type": "string", "description": "e.g. '500', '700', '900'"},
                    "style":       {"type": "string", "description": "'normal' | 'italic'"},
                    "case":        {"type": "string", "description": "'uppercase' | 'lowercase' | 'title' | 'as-typed'"},
                    "tracking":    {"type": "string", "description": "e.g. 'tracking-tight', '-0.02em', 'tracking-[0.2em]'"},
                    "icon":        {"type": "string", "description": "One-line description of the mark/icon if treatment has an icon; empty string otherwise"},
                    "color_token": {"type": "string", "description": "Which palette token the wordmark uses: 'foreground' | 'primary' | 'accent' | 'card_foreground'"},
                    "size_desktop":{"type": "string", "description": "e.g. '20px', '24px'"},
                    "placement":   {"type": "string", "description": "Where it sits: 'navbar_left' | 'navbar_center' | 'sidebar_top' | 'split_navbar_header'"},
                },
                "required": ["treatment", "font", "weight", "case", "tracking", "color_token", "size_desktop", "placement"],
            },
            "typography": {
                "type": "object",
                "properties": {
                    "heading_font":     {"type": "string", "description": "Google Fonts family name (e.g. 'Fraunces', 'Space Grotesk')"},
                    "heading_font_url": {"type": "string", "description": "Full Google Fonts CSS2 URL with weight/style axes"},
                    "body_font":        {"type": "string"},
                    "body_font_url":    {"type": "string"},
                    "heading_weight":   {"type": "string", "description": "e.g. '600', '700', '800'"},
                    "heading_style":    {"type": "string", "description": "'normal' or 'italic'"},
                    "body_weight":      {"type": "string"},
                    "type_scale": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "10 font-size steps in px, musical ratio (1.2-1.4). Ordered smallest to largest.",
                    },
                    "overall_vibe": {"type": "string", "description": "2-3 words, e.g. 'bookish editorial', 'crisp technical', 'bold artisan'"},
                },
                "required": ["heading_font", "heading_font_url", "body_font", "body_font_url", "heading_weight", "body_weight", "type_scale", "overall_vibe"],
            },
            "spacing": {
                "type": "object",
                "properties": {
                    "base":              {"type": "number", "description": "Base unit in px — 4 or 8"},
                    "section_padding_y": {"type": "number", "description": "Section vertical padding in px"},
                    "section_padding_x": {"type": "number"},
                    "rhythm":            {"type": "string", "description": "tight_editorial / standard_modern / airy_luxury / asymmetric / dense_information"},
                },
                "required": ["base", "section_padding_y", "section_padding_x", "rhythm"],
            },
            "card_language": {
                "type": "object",
                "properties": {
                    "radius":      {"type": "string"},
                    "border":      {"type": "string", "description": "e.g. '1px solid hsl(var(--border))' or 'none'"},
                    "shadow":      {"type": "string", "description": "e.g. 'none', 'sm', 'md'"},
                    "padding":     {"type": "string", "description": "Tailwind token like 'p-8' or '32px'"},
                    "description": {"type": "string", "description": "One-sentence plain English recipe"},
                },
                "required": ["radius", "border", "shadow", "padding", "description"],
            },
            "motion_language": {
                "type": "object",
                "properties": {
                    "enter":   {"type": "string", "description": "e.g. 'fade-up stagger 80ms', 'slide-in-from-left'"},
                    "hover":   {"type": "string"},
                    "scroll":  {"type": "string"},
                    "duration":{"type": "string", "description": "e.g. '300ms', '450ms'"},
                    "easing":  {"type": "string", "description": "CSS easing fn or curve"},
                },
                "required": ["enter", "hover", "scroll", "duration", "easing"],
            },
            "section_rhythm": {
                "type": "object",
                "description": "Per-section background/treatment pattern. Keys are section names that appear on the page.",
                "additionalProperties": {"type": "string"},
            },
            "hero_archetype":           {"type": "string", "description": "split / bento / diagonal / magazine / layered / cinematic / editorial_offset / full_bleed_dark / typographic / invented"},
            "features_archetype":       {"type": "string", "description": "bento_mixed / zigzag / vertical_tabs / horizontal_scroll / masonry / tilt_stack / showcase / timeline / numbered_editorial"},
            "signature_motif":          {"type": "string", "description": "One recurring decorative element used 2-3x (e.g. 'hairline divider with offset dot', 'hand-drawn squiggle', 'topographic contour line', 'grain texture overlay')"},
            "decorative_pattern":       {"type": "string", "description": "One low-opacity recurring texture — 'dots', 'noise', 'squiggles', 'orbs', 'topographic', or 'none'"},
            "border_radius_language":   {"type": "string", "description": "sharp / crisp / soft / pill / organic / mixed"},
            "color_application_strategy": {"type": "string", "description": "mono_accent / duotone_photos / gradient_mesh / inverted_dark / polychrome / photographic_neutral / brand_flood"},
            "hover_interaction_style":  {"type": "string", "description": "lift_and_shadow / tilt_3d / reveal_content / glow_ring / morph_shape / invert_colors / magnetic_cursor"},
            "chart_colors":             {"type": "array", "items": {"type": "string"}, "description": "5 HSL values for data viz, harmonious with palette."},
            "banned_patterns":          {"type": "array", "items": {"type": "string"}, "description": "3-5 design moves you explicitly reject for this project"},
            "distinctive_moves":        {"type": "array", "items": {"type": "string"}, "description": "3-5 specific patterns that MUST appear to make this design identifiable"},

            # ─── ADMIN / CRM / TMS / SAAS DASHBOARD / ECOMMERCE FIELDS ──────────
            # These are REQUIRED when layout_archetype is admin_dashboard, crm,
            # tms, saas_dashboard, or ecommerce. They are optional otherwise.
            "density_mode": {
                "type": "string",
                "description": "ADMIN ONLY. 'compact' (power users — 28-32px rows) | 'comfortable' (40-44px rows) | 'spacious' (48-56px rows). Pick based on data-volume expectation.",
            },
            "status_palette": {
                "type": "object",
                "description": "ADMIN ONLY. Semantic status colors SEPARATE from brand accent. Each has WCAG AA contrast when used on a badge (fill + foreground).",
                "properties": {
                    "success":            {"type": "string", "description": "HSL bare — e.g. '142 72% 29%'"},
                    "success_foreground": {"type": "string"},
                    "warning":            {"type": "string"},
                    "warning_foreground": {"type": "string"},
                    "info":               {"type": "string"},
                    "info_foreground":    {"type": "string"},
                    "neutral":            {"type": "string"},
                    "neutral_foreground": {"type": "string"},
                    "error":              {"type": "string"},
                    "error_foreground":   {"type": "string"},
                },
            },
            "table_language": {
                "type": "object",
                "description": "ADMIN ONLY. DataTable visual recipe.",
                "properties": {
                    "row_height":        {"type": "string", "description": "e.g. '36px', '44px', '56px'"},
                    "border_style":      {"type": "string", "description": "'hairline_rows' | 'zebra' | 'bordered_cells' | 'borderless' | 'hairline_cols_only'"},
                    "header_weight":     {"type": "string", "description": "e.g. '500', '600', 'uppercase_xs_500'"},
                    "header_background": {"type": "string", "description": "'transparent' | 'muted' | 'card'"},
                    "hover_treatment":   {"type": "string", "description": "'muted' | 'card' | 'primary_5' | 'none'"},
                    "cell_padding":      {"type": "string", "description": "e.g. '12px 16px'"},
                    "description":       {"type": "string", "description": "One-sentence English recipe"},
                },
            },
            "form_language": {
                "type": "object",
                "description": "ADMIN ONLY. Form input recipe.",
                "properties": {
                    "label_position":  {"type": "string", "description": "'above' | 'floating' | 'inline_left' | 'inline_right'"},
                    "label_style":     {"type": "string", "description": "e.g. 'text-sm font-medium text-foreground mb-1.5'"},
                    "input_style":     {"type": "string", "description": "'outlined' | 'underlined' | 'filled_muted' | 'filled_card_with_border'"},
                    "input_height":    {"type": "string", "description": "e.g. '36px', '40px', '44px'"},
                    "focus_style":     {"type": "string", "description": "e.g. 'ring-2 ring-primary/40 border-primary' or '2px solid primary outline'"},
                    "error_style":     {"type": "string", "description": "e.g. 'text-destructive text-xs mt-1 + border-destructive'"},
                    "spacing_between": {"type": "string", "description": "e.g. '20px', '24px'"},
                    "description":     {"type": "string"},
                },
            },
            "sidebar_language": {
                "type": "object",
                "description": "ADMIN ONLY. Sidebar navigation recipe.",
                "properties": {
                    "width":             {"type": "string", "description": "e.g. '256px', '280px', '72px' (icon-only)"},
                    "variant":           {"type": "string", "description": "'wide_labelled' | 'icon_plus_labels' | 'icon_only' | 'rail_plus_panel'"},
                    "background":        {"type": "string", "description": "Palette token: 'card' | 'background' | 'foreground' (for dark sidebar)"},
                    "active_treatment":  {"type": "string", "description": "e.g. 'primary_10_bg_plus_primary_text' | 'left_border_primary_2px' | 'pill_filled_primary'"},
                    "section_divider":   {"type": "string", "description": "'uppercase_label' | 'hairline_rule' | 'spacing_only'"},
                    "icon_size":         {"type": "string", "description": "e.g. '16px', '18px', '20px'"},
                    "collapsed_behavior":{"type": "string", "description": "'collapse_to_icon_rail' | 'hide_entirely' | 'none'"},
                    "description":       {"type": "string"},
                },
            },
            "toolbar_language": {
                "type": "object",
                "description": "ADMIN ONLY. Page toolbar (search + filters + bulk actions + primary CTA).",
                "properties": {
                    "search_chrome":     {"type": "string", "description": "'rounded_filled_muted' | 'underlined' | 'outlined_with_cmd_k'"},
                    "filter_style":      {"type": "string", "description": "'chip_pills' | 'dropdown_buttons' | 'segmented_control' | 'advanced_drawer'"},
                    "bulk_action_style": {"type": "string", "description": "'sticky_bar_bottom' | 'inline_chip_row' | 'contextual_menu'"},
                    "primary_cta":       {"type": "string", "description": "e.g. 'filled primary with plus icon, top-right'"},
                    "description":       {"type": "string"},
                },
            },
            "empty_state_language": {
                "type": "object",
                "description": "ADMIN ONLY. How empty states look (first-run, search-no-results, filter-no-match).",
                "properties": {
                    "illustration_style": {"type": "string", "description": "'line_icon' | 'duotone_spot' | 'photographic' | 'abstract_motif' | 'none'"},
                    "copy_tone":          {"type": "string", "description": "e.g. 'direct and helpful', 'warm encouraging'"},
                    "cta_placement":      {"type": "string", "description": "'centered_button_below' | 'link_in_body' | 'dual_primary_secondary'"},
                    "description":        {"type": "string"},
                },
            },
            "data_viz_style": {
                "type": "object",
                "description": "ADMIN ONLY. Chart/graph treatment beyond just colors.",
                "properties": {
                    "line_weight":   {"type": "string", "description": "e.g. '2px', '1.5px', '3px'"},
                    "axis_style":    {"type": "string", "description": "'hidden' | 'hairline_muted' | 'bold_grid'"},
                    "grid_style":    {"type": "string", "description": "'none' | 'dotted_horizontal' | 'solid_muted'"},
                    "tooltip_style": {"type": "string", "description": "'floating_card_shadow' | 'inline_pill' | 'dashed_crosshair'"},
                    "point_style":   {"type": "string", "description": "'filled_circle' | 'hollow_ring' | 'none_hover_reveal'"},
                    "description":   {"type": "string"},
                },
            },
        },
        "required": [
            "design_system_name", "archetype", "personality", "palette", "radius",
            "radius_tokens", "brand_mark",
            "typography", "spacing", "card_language", "motion_language",
            "section_rhythm", "hero_archetype", "features_archetype",
            "signature_motif", "decorative_pattern", "border_radius_language",
            "color_application_strategy", "hover_interaction_style",
            "chart_colors", "banned_patterns", "distinctive_moves",
        ],
    },
}

# Layout archetypes that need the admin UI blocks
_ADMIN_ARCHETYPES = {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce", "internal_tool", "saas_app"}


def _is_admin_archetype(layout_archetype: str) -> bool:
    return (layout_archetype or "").strip().lower() in _ADMIN_ARCHETYPES


# ─── User-prompt builder ────────────────────────────────────────────────────

def _build_user_prompt(
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    vibe: str,
    retry_feedback: Optional[str] = None,
) -> str:
    feedback_block = ""
    if retry_feedback:
        feedback_block = f"""
⚠️ PREVIOUS ATTEMPT FAILED VALIDATION:
{retry_feedback}

Fix ONLY the flagged issues. Keep everything else the same personality. Re-emit the tool call.
"""

    is_admin = _is_admin_archetype(layout_archetype)

    if is_admin:
        archetype_block = f"""
THIS PROJECT IS AN ADMIN / CRM / TMS / SAAS DASHBOARD / ECOMMERCE APP.
You MUST also emit these admin-specific fields:
  - density_mode (compact | comfortable | spacious)
  - status_palette (success / warning / info / neutral / error — each with foreground, ALL WCAG AA on badges)
  - table_language (row_height, border_style, header_weight, hover_treatment, cell_padding)
  - form_language (label_position, input_style, focus_style, error_style)
  - sidebar_language (width, variant, background, active_treatment, section_divider)
  - toolbar_language (search_chrome, filter_style, bulk_action_style, primary_cta)
  - empty_state_language (illustration_style, copy_tone, cta_placement)
  - data_viz_style (line_weight, axis_style, grid_style, tooltip_style)

Admin design rules:
  - Density is tight. section_padding_y is 32-64px, NOT 96+. Rows are 36-44px.
  - Status colors NEVER equal the brand accent.
  - Sidebar is the primary nav. Specify which palette token backs it.
  - Table, form, sidebar, toolbar ALL share the same border_radius_language.
  - Think about WHICH real SaaS you'd steal from: Linear, Attio, Retool, Ramp,
    Notion, Stripe Dashboard, Vercel Dashboard, Plaid Console. Not Salesforce
    Classic. Not 2018 Material admin templates.
"""
        think_block = """Think through these before emitting:
  1. What is the emotional register this tool should hit? (precise? approachable? terminal-serious?)
  2. Which archetype fits a work-focused app? (neo_swiss / sharp_corporate / minimal_luxe / brutalist_mono)
  3. Density: what's the data volume? Compact for power users, comfortable for execs.
  4. Sidebar variant: icon+labels or icon-only rail? Match the density.
  5. Table style: zebra vs hairline vs borderless? Which fits the brand's precision?
  6. Status palette: pick 5 colors that are semantically clear AND look like they
     belong to this brand — not generic bootstrap red/yellow/green.
  7. Typography: body font MUST be legible at 13-14px for dense tables.
"""
    else:
        archetype_block = ""
        think_block = """Think through these before emitting:
  1. What is the emotional register this brand should hit? (confident? gentle? precise? playful?)
  2. Which of the 17 archetypes fits, or which blend?
  3. What would a senior designer at a top studio choose for palette — specific HSLs,
     not generic Tailwind defaults?
  4. What font pairing earns this brand its personality?
  5. What is the ONE signature motif that becomes the design's fingerprint?
  6. What dated patterns MUST be banned here?
  7. What distinctive moves MUST appear?
  8. Brand mark: wordmark vs icon+wordmark vs monogram? Pick the font and exact case/tracking.
"""

    return f"""Design the bespoke design system for this project.

PROJECT DESCRIPTION: {description}
BRAND NAME: {brand_name}
DOMAIN: {domain}
LAYOUT TYPE: {layout_archetype}
COPY TONE: {copy_tone or '(not specified — infer from domain)'}
VIBE KEYWORDS: {vibe or '(not specified — infer from description)'}
{archetype_block}
{think_block}{feedback_block}
Call the emit_design_system tool with the complete, internally consistent system."""


# ─── Validation — contrast, type scale, grid ────────────────────────────────

def _hsl_to_rgb(hsl: str) -> Optional[tuple[float, float, float]]:
    """Parse bare HSL string '22 85% 45%' → (r, g, b) in 0-1. Returns None on parse failure."""
    try:
        parts = hsl.strip().replace("%", "").split()
        if len(parts) < 3:
            return None
        h = float(parts[0]) % 360
        s = float(parts[1]) / 100.0
        l = float(parts[2]) / 100.0
    except (ValueError, IndexError):
        return None

    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if   h < 60:  r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return (r + m, g + m, b + m)


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def _chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


def _contrast_ratio(hsl_a: str, hsl_b: str) -> float:
    """WCAG contrast ratio, ≥4.5 for AA body, ≥3.0 for AA large text."""
    rgb_a = _hsl_to_rgb(hsl_a)
    rgb_b = _hsl_to_rgb(hsl_b)
    if not rgb_a or not rgb_b:
        return 0.0
    la = _relative_luminance(rgb_a)
    lb = _relative_luminance(rgb_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def validate_design_system(design: dict, layout_archetype: str = "") -> list[str]:
    """Return a list of human-readable violations. Empty list = passes.

    layout_archetype branches the validator: admin archetypes add checks for
    density, status_palette contrast, required admin blocks, and tighter
    section padding. Landing archetypes enforce generous section padding.
    """
    violations: list[str] = []
    palette = design.get("palette") or {}
    is_admin = _is_admin_archetype(layout_archetype)

    # Contrast checks on critical pairs
    contrast_pairs = [
        ("foreground", "background", 4.5, "body text"),
        ("primary_foreground", "primary", 4.5, "primary button"),
        ("secondary_foreground", "secondary", 4.5, "secondary button"),
        ("accent_foreground", "accent", 4.5, "accent button"),
        ("muted_foreground", "muted", 3.5, "muted caption"),
        ("card_foreground", "card", 4.5, "card text"),
        ("destructive_foreground", "destructive", 4.5, "destructive button"),
    ]
    for fg_key, bg_key, min_ratio, label in contrast_pairs:
        fg = palette.get(fg_key) or ""
        bg = palette.get(bg_key) or ""
        if not fg or not bg:
            violations.append(f"Palette missing {fg_key} or {bg_key}")
            continue
        ratio = _contrast_ratio(fg, bg)
        if ratio < min_ratio:
            violations.append(
                f"Contrast too low for {label}: {fg_key} on {bg_key} = {ratio:.2f}:1 "
                f"(need ≥{min_ratio}:1). Darken foreground or lighten background."
            )

    # Generic-template bans
    def _is_default_blue(hsl: str) -> bool:
        rgb = _hsl_to_rgb(hsl)
        if not rgb:
            return False
        # Tailwind default blue-600 ≈ hsl(221 83% 53%) → rgb ~ (37, 99, 235) / 255
        r, g, b = rgb
        return (abs(r - 37/255) < 0.04 and abs(g - 99/255) < 0.04 and abs(b - 235/255) < 0.06)

    primary = palette.get("primary", "")
    if primary and _is_default_blue(primary):
        violations.append(
            "Primary is default Tailwind blue (~221 83% 53%). "
            "Pick a bespoke accent tied to the domain personality."
        )

    # Typography validation
    typo = design.get("typography") or {}
    heading = (typo.get("heading_font") or "").strip().lower()
    body = (typo.get("body_font") or "").strip().lower()
    if heading and body and heading == body:
        # Same family is OK ONLY if heading weight/style differs dramatically
        h_style = (typo.get("heading_style") or "normal").lower()
        if h_style != "italic":
            violations.append(
                f"Heading font equals body font ({heading}) and heading style is not italic. "
                "Pick a display font for headings OR set heading_style to 'italic' for clear contrast."
            )

    scale = typo.get("type_scale") or []
    if len(scale) >= 3:
        ratios = [scale[i+1] / scale[i] for i in range(len(scale) - 1) if scale[i] > 0]
        if ratios:
            avg_ratio = sum(ratios) / len(ratios)
            if avg_ratio < 1.12 or avg_ratio > 1.5:
                violations.append(
                    f"Type scale average ratio {avg_ratio:.2f} outside musical range [1.15, 1.45]. "
                    "Rebuild scale with ~1.25 or ~1.333 ratio."
                )

    # Spacing grid
    spacing = design.get("spacing") or {}
    base = spacing.get("base")
    if base not in (4, 8):
        violations.append(f"Spacing base must be 4 or 8 (got {base}).")
    spy = spacing.get("section_padding_y") or 0
    if base and spy and spy % base != 0:
        violations.append(
            f"section_padding_y ({spy}) not a multiple of base ({base}). Re-snap to grid."
        )
    # Archetype-conditional padding: landing pages need breathing room,
    # admin/CRM/TMS need dense scanning.
    if spy:
        if is_admin:
            if spy < 24 or spy > 80:
                violations.append(
                    f"section_padding_y {spy}px is wrong for admin/CRM/TMS — use 32-64px. "
                    "Admin UIs are for scanning, not scrolling."
                )
        else:
            if spy < 72:
                violations.append(
                    f"section_padding_y {spy}px is too tight for a landing page — use 96px+."
                )

    # Font URLs must be Google Fonts CSS2 endpoints
    for key in ("heading_font_url", "body_font_url"):
        url = typo.get(key) or ""
        if url and "fonts.googleapis.com" not in url:
            violations.append(f"{key} is not a Google Fonts URL: {url[:80]}")

    # Brand mark — every project gets one
    bm = design.get("brand_mark") or {}
    if not bm.get("treatment") or not bm.get("font"):
        violations.append(
            "brand_mark missing treatment or font. Every project needs a wordmark/monogram "
            "so the navbar is never a bare nav with no logo."
        )

    # Radius tokens — ensure they exist and share a language
    rt = design.get("radius_tokens") or {}
    if not rt.get("button") or not rt.get("card"):
        violations.append(
            "radius_tokens missing button or card radius. Every element needs an "
            "explicit radius so the border-radius language is applied consistently."
        )

    # Admin-only: required admin blocks + status palette contrast
    if is_admin:
        required_admin_blocks = [
            "density_mode", "status_palette", "table_language", "form_language",
            "sidebar_language", "toolbar_language", "empty_state_language", "data_viz_style",
        ]
        for block_key in required_admin_blocks:
            if not design.get(block_key):
                violations.append(f"Admin archetype requires {block_key}. Emit this block.")

        dm = (design.get("density_mode") or "").lower()
        if dm and dm not in ("compact", "comfortable", "spacious"):
            violations.append(
                f"density_mode '{dm}' must be compact | comfortable | spacious."
            )

        status = design.get("status_palette") or {}
        status_pairs = [
            ("success_foreground", "success", "success badge"),
            ("warning_foreground", "warning", "warning badge"),
            ("info_foreground", "info", "info badge"),
            ("error_foreground", "error", "error badge"),
        ]
        for fg_key, bg_key, label in status_pairs:
            fg = status.get(fg_key) or ""
            bg = status.get(bg_key) or ""
            if fg and bg:
                r = _contrast_ratio(fg, bg)
                if r < 4.5:
                    violations.append(
                        f"status_palette {label} contrast {r:.2f}:1 < 4.5:1. "
                        "Darken foreground or lighten background."
                    )

        # Status ≠ brand accent (otherwise success looks like a CTA)
        primary_hsl = palette.get("primary") or ""
        success_hsl = status.get("success") or ""
        if primary_hsl and success_hsl and primary_hsl.strip() == success_hsl.strip():
            violations.append(
                "status.success equals palette.primary — status colors MUST be separate "
                "from the brand accent so users can distinguish CTA from positive state."
            )

    return violations


# ─── Claude caller ──────────────────────────────────────────────────────────

async def _call_claude(
    system: str,
    user: str,
    api_key: str,
) -> Optional[dict]:
    """Call Claude with forced tool output. Returns the tool input dict, or None on failure."""
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.45,  # a bit of taste variance — not deterministic
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [_DESIGN_TOOL],
        "tool_choice": {"type": "tool", "name": "emit_design_system"},
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_API_URL, headers=headers, json=payload)
    except Exception as exc:
        logger.warning("Design Director Claude call exception: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning("Design Director Claude error %d: %s", resp.status_code, resp.text[:300])
        return None

    try:
        data = resp.json()
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "emit_design_system":
                return block.get("input") or {}
    except Exception as exc:
        logger.warning("Design Director response parse failed: %s", exc)
    return None


# ─── Public orchestrator ────────────────────────────────────────────────────

async def build_design_system(
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    api_key: str,
    vibe: str = "",
    websocket=None,
) -> Optional[dict]:
    """One Claude call that designs a bespoke, validated design system.

    Retries once with violation feedback on validation failure. Returns None
    if both attempts fail — caller falls back to old research-based flow.

    FAIL-SOFT: any exception logs a warning and returns None.
    """
    if not api_key:
        logger.info("Design Director skipped — no Anthropic API key")
        return None

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": "🎨 Designing bespoke design system...",
            })
        except Exception:
            pass

    # Attempt 1
    prompt = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
    )
    design = await _call_claude(_SYSTEM_PROMPT, prompt, api_key)
    if not design:
        logger.warning("Design Director attempt 1 returned no design")
        return None

    violations = validate_design_system(design, layout_archetype=layout_archetype)
    if not violations:
        logger.info(
            "Design Director attempt 1 valid — name=%s archetype=%s",
            design.get("design_system_name"), design.get("archetype"),
        )
        if websocket is not None:
            try:
                await websocket.send_json({
                    "type": "progress",
                    "content": f"✅ Design system locked: {design.get('design_system_name', 'Custom')} ({design.get('archetype', '')})",
                })
            except Exception:
                pass
        return design

    logger.info("Design Director attempt 1 had %d violations — retrying", len(violations))
    feedback = "\n".join(f"- {v}" for v in violations)

    # Attempt 2
    prompt_retry = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        retry_feedback=feedback,
    )
    design2 = await _call_claude(_SYSTEM_PROMPT, prompt_retry, api_key)
    if not design2:
        logger.warning("Design Director retry returned no design — using attempt 1 anyway")
        return design  # better than nothing

    violations2 = validate_design_system(design2, layout_archetype=layout_archetype)
    if violations2:
        logger.warning(
            "Design Director retry still has %d violations: %s — accepting anyway",
            len(violations2), "; ".join(violations2[:3]),
        )
    else:
        logger.info("Design Director retry valid after feedback")

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": f"✅ Design system locked: {design2.get('design_system_name', 'Custom')}",
            })
        except Exception:
            pass

    return design2


# ─── Rendering: design dict → research text blocks ──────────────────────────

def _render_css_variables(design: dict) -> str:
    p = design.get("palette") or {}
    radius = design.get("radius") or "0.5rem"
    dark = design.get("dark_palette") or {}

    lines = [
        f"--primary: {p.get('primary', '')} | --primary-foreground: {p.get('primary_foreground', '')}",
        f"--secondary: {p.get('secondary', '')} | --secondary-foreground: {p.get('secondary_foreground', '')}",
        f"--accent: {p.get('accent', '')} | --accent-foreground: {p.get('accent_foreground', '')}",
        f"--background: {p.get('background', '')} | --foreground: {p.get('foreground', '')}",
        f"--card: {p.get('card', '')} | --card-foreground: {p.get('card_foreground', '')}",
        f"--muted: {p.get('muted', '')} | --muted-foreground: {p.get('muted_foreground', '')}",
        f"--border: {p.get('border', '')} | --ring: {p.get('ring', '')}",
        f"--destructive: {p.get('destructive', '')} | --destructive-foreground: {p.get('destructive_foreground', '')}",
        f"--radius: {radius}",
    ]
    if dark:
        lines.append("")
        lines.append("[DARK MODE OVERRIDES]")
        for k, v in dark.items():
            lines.append(f"--{k.replace('_', '-')}: {v}")
    return "\n".join(lines)


def _render_fonts(design: dict) -> str:
    t = design.get("typography") or {}
    scale = t.get("type_scale") or []
    body_size = scale[2] if len(scale) > 2 else 16
    hero_size = scale[-1] if scale else 72
    h2_size = scale[-3] if len(scale) > 3 else 40
    return (
        f"heading: {t.get('heading_font', '')} ({t.get('heading_font_url', '')})\n"
        f"body: {t.get('body_font', '')} ({t.get('body_font_url', '')})\n"
        f"hero_size: {hero_size}px / 1.05 / -0.02em\n"
        f"h2_size: {h2_size}px / weight {t.get('heading_weight', '700')}\n"
        f"body_size: {body_size}px / 1.6\n"
        f"overall_vibe: {t.get('overall_vibe', '')}"
    )


def _render_palette_notes(design: dict) -> str:
    p = design.get("palette") or {}
    strategy = design.get("color_application_strategy") or ""
    return (
        f"strategy: {strategy}\n"
        f"background: {p.get('background', '')} | foreground: {p.get('foreground', '')}\n"
        f"primary (accent): {p.get('primary', '')} on {p.get('primary_foreground', '')}\n"
        f"accent: {p.get('accent', '')} on {p.get('accent_foreground', '')}\n"
        f"muted text: {p.get('muted_foreground', '')} on {p.get('muted', '')}\n"
        f"chart_colors: {', '.join(design.get('chart_colors') or [])}"
    )


def _render_typography_notes(design: dict) -> str:
    t = design.get("typography") or {}
    scale = t.get("type_scale") or []
    return (
        f"heading: {t.get('heading_font', '')} weight {t.get('heading_weight', '')}, style {t.get('heading_style', 'normal')}\n"
        f"body: {t.get('body_font', '')} weight {t.get('body_weight', '')}\n"
        f"type_scale (px): {scale}\n"
        f"overall_vibe: {t.get('overall_vibe', '')}"
    )


def _render_brand_mark(design: dict) -> str:
    bm = design.get("brand_mark") or {}
    return (
        f"treatment: {bm.get('treatment', 'wordmark')}\n"
        f"font: {bm.get('font', '')}\n"
        f"weight: {bm.get('weight', '700')}\n"
        f"style: {bm.get('style', 'normal')}\n"
        f"case: {bm.get('case', 'as-typed')}\n"
        f"tracking: {bm.get('tracking', '-0.01em')}\n"
        f"icon: {bm.get('icon', '')}\n"
        f"color_token: {bm.get('color_token', 'foreground')}\n"
        f"size_desktop: {bm.get('size_desktop', '22px')}\n"
        f"placement: {bm.get('placement', 'navbar_left')}\n"
        f"\n"
        f"INSTRUCTION: Every Header/Navbar/Sidebar MUST render this brand mark. "
        f"Never output a navbar with only menu links and no logo."
    )


def _render_radius_tokens(design: dict) -> str:
    rt = design.get("radius_tokens") or {}
    radius = design.get("radius") or "0.5rem"
    lang = design.get("border_radius_language") or ""
    lines = [
        f"base: {radius}",
        f"language: {lang}",
        f"button: {rt.get('button', radius)}",
        f"input: {rt.get('input', radius)}",
        f"card: {rt.get('card', radius)}",
        f"badge: {rt.get('badge', radius)}",
        f"image: {rt.get('image', radius)}",
        f"modal: {rt.get('modal', radius)}",
        f"tooltip: {rt.get('tooltip', radius)}",
        "",
        "INSTRUCTION: Every radius in the generated code MUST match one of these "
        "tokens. No ad-hoc values like rounded-xl if cards use rounded-md.",
    ]
    return "\n".join(lines)


def _render_admin_ui_language(design: dict) -> str:
    density = design.get("density_mode") or "comfortable"
    status = design.get("status_palette") or {}
    table = design.get("table_language") or {}
    form = design.get("form_language") or {}
    sidebar = design.get("sidebar_language") or {}
    toolbar = design.get("toolbar_language") or {}
    empty = design.get("empty_state_language") or {}
    viz = design.get("data_viz_style") or {}

    return (
        f"density_mode: {density}\n"
        f"\n"
        f"status_palette:\n"
        f"  success: {status.get('success', '')} on {status.get('success_foreground', '')}\n"
        f"  warning: {status.get('warning', '')} on {status.get('warning_foreground', '')}\n"
        f"  info:    {status.get('info', '')} on {status.get('info_foreground', '')}\n"
        f"  neutral: {status.get('neutral', '')} on {status.get('neutral_foreground', '')}\n"
        f"  error:   {status.get('error', '')} on {status.get('error_foreground', '')}\n"
        f"\n"
        f"table:\n"
        f"  row_height: {table.get('row_height', '44px')}\n"
        f"  border_style: {table.get('border_style', 'hairline_rows')}\n"
        f"  header_weight: {table.get('header_weight', '500')}\n"
        f"  header_background: {table.get('header_background', 'transparent')}\n"
        f"  hover_treatment: {table.get('hover_treatment', 'muted')}\n"
        f"  cell_padding: {table.get('cell_padding', '12px 16px')}\n"
        f"  → {table.get('description', '')}\n"
        f"\n"
        f"form:\n"
        f"  label_position: {form.get('label_position', 'above')}\n"
        f"  label_style: {form.get('label_style', '')}\n"
        f"  input_style: {form.get('input_style', 'outlined')}\n"
        f"  input_height: {form.get('input_height', '40px')}\n"
        f"  focus_style: {form.get('focus_style', '')}\n"
        f"  error_style: {form.get('error_style', '')}\n"
        f"  spacing_between: {form.get('spacing_between', '20px')}\n"
        f"  → {form.get('description', '')}\n"
        f"\n"
        f"sidebar:\n"
        f"  width: {sidebar.get('width', '256px')}\n"
        f"  variant: {sidebar.get('variant', 'wide_labelled')}\n"
        f"  background: {sidebar.get('background', 'card')}\n"
        f"  active_treatment: {sidebar.get('active_treatment', 'primary_10_bg_plus_primary_text')}\n"
        f"  section_divider: {sidebar.get('section_divider', 'uppercase_label')}\n"
        f"  icon_size: {sidebar.get('icon_size', '18px')}\n"
        f"  collapsed_behavior: {sidebar.get('collapsed_behavior', 'none')}\n"
        f"  → {sidebar.get('description', '')}\n"
        f"\n"
        f"toolbar:\n"
        f"  search_chrome: {toolbar.get('search_chrome', 'rounded_filled_muted')}\n"
        f"  filter_style: {toolbar.get('filter_style', 'dropdown_buttons')}\n"
        f"  bulk_action_style: {toolbar.get('bulk_action_style', 'sticky_bar_bottom')}\n"
        f"  primary_cta: {toolbar.get('primary_cta', 'filled primary with plus icon, top-right')}\n"
        f"  → {toolbar.get('description', '')}\n"
        f"\n"
        f"empty_state:\n"
        f"  illustration_style: {empty.get('illustration_style', 'line_icon')}\n"
        f"  copy_tone: {empty.get('copy_tone', 'direct and helpful')}\n"
        f"  cta_placement: {empty.get('cta_placement', 'centered_button_below')}\n"
        f"  → {empty.get('description', '')}\n"
        f"\n"
        f"data_viz:\n"
        f"  line_weight: {viz.get('line_weight', '2px')}\n"
        f"  axis_style: {viz.get('axis_style', 'hairline_muted')}\n"
        f"  grid_style: {viz.get('grid_style', 'dotted_horizontal')}\n"
        f"  tooltip_style: {viz.get('tooltip_style', 'floating_card_shadow')}\n"
        f"  point_style: {viz.get('point_style', 'filled_circle')}\n"
        f"  → {viz.get('description', '')}\n"
        f"\n"
        f"INSTRUCTION: Every DataTable, form, sidebar, toolbar, empty state, and "
        f"chart in this project MUST follow the recipe above. Values here WIN over "
        f"any generic admin defaults in the phase-1/phase-2 code-gen prompts."
    )


def _render_layout_blueprint(design: dict) -> str:
    card = design.get("card_language") or {}
    motion = design.get("motion_language") or {}
    rhythm = design.get("section_rhythm") or {}
    spacing = design.get("spacing") or {}
    rhythm_lines = "\n".join(f"  {k}: {v}" for k, v in rhythm.items())

    return (
        f"design_dna_summary: {design.get('personality', '')} — archetype {design.get('archetype', '')}\n"
        f"\n"
        f"hero_archetype: {design.get('hero_archetype', '')}\n"
        f"features_archetype: {design.get('features_archetype', '')}\n"
        f"\n"
        f"card_language: radius {card.get('radius', '')} | border {card.get('border', '')} | shadow {card.get('shadow', '')} | padding {card.get('padding', '')}\n"
        f"  → {card.get('description', '')}\n"
        f"\n"
        f"typography_pairing: {(design.get('typography') or {}).get('heading_font', '')} heading + {(design.get('typography') or {}).get('body_font', '')} body\n"
        f"\n"
        f"motion_language: enter {motion.get('enter', '')} | hover {motion.get('hover', '')} | scroll {motion.get('scroll', '')} | {motion.get('duration', '')} {motion.get('easing', '')}\n"
        f"\n"
        f"decorative_pattern: {design.get('decorative_pattern', '')}\n"
        f"signature_motif: {design.get('signature_motif', '')}\n"
        f"border_radius_language: {design.get('border_radius_language', '')}\n"
        f"color_application_strategy: {design.get('color_application_strategy', '')}\n"
        f"hover_interaction_style: {design.get('hover_interaction_style', '')}\n"
        f"spacing_rhythm: {spacing.get('rhythm', '')} (base {spacing.get('base', 8)}px, section_y {spacing.get('section_padding_y', 96)}px)\n"
        f"\n"
        f"section_rhythm:\n{rhythm_lines}\n"
        f"\n"
        f"distinctive_moves:\n" + "\n".join(f"  - {m}" for m in (design.get('distinctive_moves') or [])) + "\n"
        f"\n"
        f"banned_patterns:\n" + "\n".join(f"  - {b}" for b in (design.get('banned_patterns') or [])) + "\n"
        f"\n"
        f"novelty_check: This design is bespoke to the project via archetype {design.get('archetype', '')} — validated palette contrast, musical type scale, consistent radius language."
    )


def inject_design_blocks(research: str, design: dict) -> str:
    """Replace (or inject) the design-related headers in research with the Design Director output.

    The downstream distiller and schema builder parse these exact headers:
      ===DESIGN_SYSTEM_NAME===
      ===PALETTE===
      ===TYPOGRAPHY===
      ===CSS_VARIABLES===
      ===FONTS===
      ===BRAND_MARK===        (NEW — logo/wordmark spec)
      ===RADIUS_TOKENS===     (NEW — per-element radii)
      ===LAYOUT_BLUEPRINT===
      ===ADMIN_UI_LANGUAGE=== (admin/CRM/TMS archetypes only)

    This function REMOVES any existing instances of these blocks in research
    and prepends the Design Director's rendered blocks so they take priority.
    """
    if not research or not design:
        return research

    # Remove any existing instances of these headers by splitting on each and
    # dropping the header's block contents.
    headers = [
        "===DESIGN_SYSTEM_NAME===",
        "===PALETTE===",
        "===TYPOGRAPHY===",
        "===CSS_VARIABLES===",
        "===FONTS===",
        "===BRAND_MARK===",
        "===RADIUS_TOKENS===",
        "===LAYOUT_BLUEPRINT===",
        "===ADMIN_UI_LANGUAGE===",
    ]

    stripped = research
    for header in headers:
        while header in stripped:
            start = stripped.index(header)
            rest = stripped[start + len(header):]
            next_header_pos = rest.find("===")
            if next_header_pos == -1:
                stripped = stripped[:start].rstrip()
            else:
                stripped = stripped[:start] + rest[next_header_pos:]

    blocks = [
        f"===DESIGN_SYSTEM_NAME===\n{design.get('design_system_name', '').strip()}",
        f"===CSS_VARIABLES===\n{_render_css_variables(design)}",
        f"===FONTS===\n{_render_fonts(design)}",
        f"===BRAND_MARK===\n{_render_brand_mark(design)}",
        f"===RADIUS_TOKENS===\n{_render_radius_tokens(design)}",
        f"===PALETTE===\n{_render_palette_notes(design)}",
        f"===TYPOGRAPHY===\n{_render_typography_notes(design)}",
        f"===LAYOUT_BLUEPRINT===\n{_render_layout_blueprint(design)}",
    ]

    # Only emit the admin block if the Director filled in admin fields.
    # The admin fields are optional in the schema — their presence indicates
    # the project IS admin-family.
    if design.get("density_mode") or design.get("status_palette") or design.get("table_language"):
        blocks.append(f"===ADMIN_UI_LANGUAGE===\n{_render_admin_ui_language(design)}")

    design_text = "\n\n" + "\n\n".join(blocks) + "\n\n"

    # Prepend to research so the Design Director blocks win in any parsing tie.
    return design_text + stripped.lstrip()
