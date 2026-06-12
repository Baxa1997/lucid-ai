"""Per-page Claude generator for the unified website pipeline.

Generates ONE complete page (route shell + all its section components) in
a single Claude call. Designed for parallel execution — pages have no
runtime dependency on each other, only on the deterministic foundation
files (globals.css, site.js, navigation.js, design-system.js).

Convention:
  Each page's components live under   src/components/pages/<slug>/
  Page composition file lives at      src/app/<route>/page.js
  Home page composition file is       src/app/page.js  (slug="home")

This isolates pages so a failure in one doesn't break others, and so each
page can have its own visual rhythm (e.g. /menu may use food-photography
heavy hero; /about may use editorial-asymmetric hero — both legitimate).

The visual_dna is the LEAD directive — every page receives the same DNA
so all pages share the same brand DNA, just expressed for that page's
purpose.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


def _content_separation_enabled() -> bool:
    """Feature flag, default ON. Set CONTENT_SEPARATION_ENABLED=0 to roll back."""
    raw = os.environ.get("CONTENT_SEPARATION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _per_section_codegen_enabled() -> bool:
    """Per-section codegen for website pages (default ON).

    OFF (=0/false): one Claude call per page generates all sections together
    — cheaper but produces formulaic patterns (Claude juggles 4-6 sections
    in one call → falls back to safe templates).

    ON  (default): one Claude call PER SECTION (matches landing pipeline).
    Each section gets focused prompt + full anatomy from visual_dna →
    distinctive, research-grounded designs. ~30-50% more Anthropic spend
    but landing-equivalent per-section quality.
    """
    raw = os.environ.get("WEBSITE_PER_SECTION_CODEGEN_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")

# Per-page generation params. Each page emits 1 composition file + 3-8
# section components. Content-rich pages (menus, locations, catalogs) can
# blow past 16K; cap at the Sonnet 4.6 native ceiling so we never truncate.
_PAGE_MAX_TOKENS = 64000
_PAGE_TIMEOUT_S = 600.0   # 10 min — must cover 64K-token Sonnet 4.6 streams.
                          # At ~150 tok/s a full 64K page = ~7 min; 120s killed
                          # every page mid-stream and burned the full token budget
                          # with zero output. Don't lower without also lowering
                          # _PAGE_MAX_TOKENS in lockstep.
_PAGE_MAX_ATTEMPTS = 2


_VALID_COMPONENT = re.compile(r"^[A-Z][A-Za-z0-9_]*$")


def _slug_from_route(route: str) -> str:
    """Convert '/menu' → 'menu', '/' → 'home', '/private-events' → 'private-events'."""
    r = (route or "").strip().strip("/")
    return r or "home"


def _slug_pascal(slug: str) -> str:
    """'private-events' → 'PrivateEvents'."""
    parts = re.split(r"[^A-Za-z0-9]+", slug)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Home"


def _section_component_name(slug: str, section_type: str) -> str:
    """Build a per-page section component name.

    Examples:
      slug='home', type='hero'      → 'HomeHero'
      slug='menu', type='showcase'  → 'MenuShowcase'
      slug='about', type='story'    → 'AboutStory'
    """
    page_pas = _slug_pascal(slug)
    sec_pas = _slug_pascal(section_type or "section")
    name = page_pas + sec_pas
    return name if _VALID_COMPONENT.match(name) else "PageSection"


def _section_anatomy_for(visual_dna: dict, section_type: str) -> str:
    """Pull the structural anatomy spec for a given section type from
    the extracted visual_dna. Returns empty string if missing.

    Defensive: real visual_dna emits anatomies as strings (per the
    extractor prompt - "ONE PARAGRAPH per section"), but bad/legacy
    upstream data can occasionally produce dicts. Coerce to string
    so we don't AttributeError on .strip().
    """
    anatomies = (visual_dna or {}).get("section_anatomies") or {}
    raw = anatomies.get(section_type)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        # Pick a useful field if present, otherwise JSON-encode.
        for k in ("description", "anatomy", "layout", "spec"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        try:
            import json as _json
            return _json.dumps(raw, ensure_ascii=False)[:300]
        except Exception:
            return ""
    return str(raw).strip()


def _build_system_prompt(
    *,
    brand_name: str,
    tagline: str,
    domain: str,
    visual_dna: dict,
    foundation_imports: dict,
) -> str:
    """System prompt: brand DNA + foundation contract. Identical across all pages
    so every page receives the same design grammar — that's what creates
    cross-page visual cohesion."""
    vd = visual_dna or {}
    intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
    motifs = vd.get("decorative_motifs") or []
    textures = vd.get("signature_textures") or []
    icons = vd.get("iconography_anchors") or []
    photo = (vd.get("photography_style") or "").strip()
    palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
    type_voice = (vd.get("typography_voice") or "").strip()
    layout_sig = (vd.get("layout_signature") or "").strip()

    intensity_note = (
        "Cultural cues sit in ACCENT POSITIONS only. The page reads "
        "modern-upscale with cultural FLAVOR — restraint stays."
        if intensity == "subtle"
        else "Cultural cues take a STRONGER role — larger textures, decorative "
             "motifs as dividers, iconography woven into headings, full-bleed "
             "cultural patterns where appropriate. UNMISTAKABLY of this category."
    )

    motifs_block = ""
    if motifs:
        motifs_block = "\n".join(f"  - {m}" for m in motifs[:6])
        motifs_block = f"\n\nDECORATIVE MOTIFS (use as accents — pick ones that fit each section):\n{motifs_block}"
    textures_block = ""
    if textures:
        textures_block = "\n".join(f"  - {t}" for t in textures[:4])
        textures_block = f"\n\nSIGNATURE TEXTURES:\n{textures_block}"
    icons_block = ""
    if icons:
        icons_block = ", ".join(icons[:6])
        icons_block = f"\n\nICONOGRAPHY ANCHORS: {icons_block}"

    foundation_block = "\n".join(
        f"  - {key} — {desc}" for key, desc in foundation_imports.items()
    )

    return f"""You are a senior frontend designer-engineer generating ONE COMPLETE PAGE for a brand-coherent multi-page website.

BRAND
  Name: {brand_name}
  Tagline: {tagline}
  Domain: {domain}

VISUAL DNA — THIS IS THE PRIMARY DESIGN DIRECTIVE
  Cultural intensity: {intensity}
    → {intensity_note}

  Cultural palette emphasis: {palette_emph or '(use foundation palette as-is)'}
  Typography voice: {type_voice or '(neutral)'}
  Photography style: {photo or '(use brand-appropriate stock)'}
  Layout signature: {layout_sig or '(modern responsive grid)'}{motifs_block}{textures_block}{icons_block}

FOUNDATION ALREADY EXISTS — IMPORT FROM THESE (do NOT regenerate):
{foundation_block}

OUTPUT CONTRACT
  Return JSON: {{"files": [{{"path": "src/...", "content": "..."}}, ...]}}
  Every file is a complete, self-contained JSX file.
  Use Tailwind CSS classes only — NO inline styles, NO css-in-js, NO css variable strings.
  Import design tokens from '@/lib/design-system' as `ds` for spacing/typography classes:
    {{ds.section}}     → section vertical padding
    {{ds.container}}   → max-width container
    {{ds.heading}}     → display font class
    {{ds.body}}        → body font class
    {{ds.card}}        → card styling
    {{ds.buttonPrimary}}, {{ds.buttonSecondary}}, {{ds.buttonGhost}}
    {{...ds.motion}}   → framer-motion in-view animation props

QUALITY BAR
  - Each section MUST visibly reflect VISUAL_DNA in ≥2 ways (color emphasis, motif, iconography, typography, layout signature).
  - A generic "centered text + 3-col grid + photo" output that could fit any brand is a FAILURE.
  - Make this page feel cohesive — section spacing, color flow, and motif placement should feel like ONE designed unit, not Lego.
  - No lorem ipsum. Write real content matching the brand voice.
"""


def _build_collections_block(data_model: Any) -> str:
    """Render the prompt section telling Claude which sections are live
    (fetched from Supabase) vs static (read from content JSON).

    Returns an empty string when there are no collections — most landing
    pages and any project where Stage 4.5 produced an empty DataModel.
    Adding the block conditionally keeps the prompt size minimal for the
    common case.

    Why a server component path
    ---------------------------
    `await getCollection(...)` only works in a server component (Next.js
    server-side rendering). Client components — the default for sections
    that use hooks or onClick — can't await a top-level call. So we
    instruct Claude to drop the `"use client"` directive AND switch the
    component to `async function` for the matching sections only.
    """
    if data_model is None or not getattr(data_model, "tables", None):
        return ""

    table_lines: list[str] = []
    for t in data_model.tables:
        field_names = ", ".join(f.name for f in t.fields[:8])
        table_lines.append(
            f"  • {t.name}  (label: {t.plural_label})\n"
            f"      fields: {field_names}"
        )
    tables_str = "\n".join(table_lines)

    return f"""

══ LIVE COLLECTIONS (Supabase-backed — server components) ══
This project has a database backend. The following collections hold
LIVE rows that the user will edit over time. For sections whose
primary purpose is to display a list of items from one of these
collections, fetch the data at render time INSTEAD of reading it from
content.<key>.items.

AVAILABLE COLLECTIONS:
{tables_str}

WHEN TO FETCH LIVE
- If a section type / purpose obviously matches one of these tables
  (e.g. "menu" / "menu_preview" → menu_items, "gallery" → gallery_images,
  "testimonials" → testimonials, "team" / "instructors" → team_members),
  treat it as a LIVE COLLECTION section.
- If no table obviously matches, treat the section as a normal static
  section using the JSON content path described below.

LIVE COLLECTION SECTION RULES (NON-NEGOTIABLE)
1. DROP the `"use client"` directive from the section file.
2. Make the component an async function:
       export default async function MenuPreviewSection() {{ ... }}
3. Import the fetcher at the top of the file:
       import {{ getCollection }} from "@/lib/db";
4. Fetch the rows inside the function body:
       const items = await getCollection("menu_items");
5. Render the rows with `items.map(...)`. Each row has the columns
   declared in the AVAILABLE COLLECTIONS list above, plus `id`,
   `created_at`, `updated_at` (autopopulated). Do NOT assume any
   columns not in the field list.
6. The section's TITLE / INTRO / CTA copy STILL comes from
   content.<section_key>.<field> in the page's JSON. Only the LIST
   OF ITEMS comes from getCollection(). Example:
       <Editable path="menu.title" type="text">
         <h2>{{content.menu.title}}</h2>
       </Editable>
       {{items.map((it) => (
         <div key={{it.id}}>
           <h3>{{it.name}}</h3>
           <p>${{(it.price_cents / 100).toFixed(2)}}</p>
         </div>
       ))}}
7. For the content JSON file: the matching `<section_key>` key MUST
   still exist with title / intro / cta_primary etc., but it MUST
   NOT contain an "items" array — items live in Supabase.
8. Do NOT wrap the items.map(...) loop with <Editable>. Editing
   individual rows happens through the admin panel, not the inline
   editor. Per-row text inside items.map(...) renders as plain JSX.

For all OTHER sections, follow the CONTENT / CODE SEPARATION block
below exactly as written (client component, "use client", import
from content.json, full Editable wrapping).
"""


def _build_user_prompt(
    *,
    page: dict,
    slug: str,
    section_specs: list[dict],
    visual_dna: dict,
    page_images: dict | None = None,
    data_model: Any = None,
) -> str:
    """User prompt: what THIS specific page should produce."""
    component_pages_dir = f"src/components/pages/{slug}"
    route = (page.get("route") or page.get("path") or "/").strip()
    page_title = (page.get("title") or _slug_pascal(slug)).strip()
    page_purpose = (page.get("purpose") or page.get("description") or "").strip()

    section_lines: list[str] = []
    for spec in section_specs:
        comp = spec["component"]
        s_type = spec["type"]
        anatomy = spec.get("anatomy") or ""
        purpose = spec.get("purpose") or ""
        line = f"  • {comp}  (type={s_type})"
        if purpose:
            line += f"  — {purpose}"
        if anatomy:
            line += f"\n      anatomy: {anatomy}"
        section_lines.append(line)

    section_block = "\n".join(section_lines) if section_lines else "  (none)"

    # Compose the import + JSX skeleton hint for the page.js composition file
    page_file = "src/app/page.js" if slug == "home" else f"src/app/{slug}/page.js"

    # Image block — only present when Stage 5.5 produced bindings
    images_block = ""
    if page_images:
        from app.services.image_binding import format_images_for_prompt
        formatted = format_images_for_prompt(page_images)
        if formatted:
            images_block = (
                "\n\n" + formatted +
                "\n\nIMAGE RULES (NON-NEGOTIABLE):\n"
                "  - Use these EXACT urls as the `src` of every <img> / <Image> tag.\n"
                "  - Use the provided `alt` text on every <img> — never empty, never \"image\".\n"
                "  - Do NOT invent paths like /images/hero.jpg — local files do not exist.\n"
                "  - Do NOT replace these with placeholders or via.placeholder.com.\n"
                "  - When a section has multiple images, use them in the order listed.\n"
                "  - Sections not listed here have NO photos — use Lucide icons or pure CSS instead.\n"
            )

    # Collections block — only present when Stage 4.5 produced a data_model
    # with at least one table AND the project is tenant-provisioned (the
    # foundation layer wrote src/lib/db.js for us).
    collections_block = _build_collections_block(data_model)

    # Detail-route hint — set on list pages (detail_template=True) so the
    # generated list cards link to the deterministically-built detail page
    # at /<route>/[slug]. Without this, list pages render but clicking
    # cards goes nowhere.
    detail_hint_block = ""
    if page.get("detail_template"):
        detail_source = (page.get("detail_source") or slug).strip().lower()
        route_slug_clean = route.lstrip("/")
        detail_hint_block = (
            "\n\n══ LIST → DETAIL ROUTE HINT ══\n"
            "This page is a LIST page paired with a detail route.\n"
            f"A detail view is already generated at /{route_slug_clean}/[slug].\n"
            "\n"
            "RULES for any card/tile/row that represents one item:\n"
            "  • Wrap it in <Link> from next/link so it navigates into the detail page:\n"
            f"      <Link href={{`/{route_slug_clean}/${{item.slug || item.id}}`}}>...</Link>\n"
            "  • Items come from a pre-built JSON data file. Import directly:\n"
            f"      import items from \"@/data/{detail_source}.json\";\n"
            "  • Map over `items` to render the list — do NOT hard-code sample\n"
            "    items inline. Each item has at minimum:\n"
            "      { id, slug, title, image_url, description, ... }\n"
            "  • Items in src/data/*.json are the LIST source; per-section\n"
            "    headers/subtitles still live in the page content JSON.\n"
        )

    content_json_path = f"src/content/pages/{slug}.json"
    content_import_path = f"@/content/pages/{slug}.json"

    if _content_separation_enabled():
        content_block = f"""
══ CONTENT / CODE SEPARATION (NON-NEGOTIABLE) ══
All visible copy MUST come from a separate JSON file. Components import
that JSON and wrap every editable element with the <Editable> component.
This is what enables in-place editing in the dashboard.

REQUIRED OUTPUT FILES (in addition to the section .jsx + page composition):

1. ONE content file at {content_json_path}
   Shape (real data, no nulls, no template stubs):
   {{
     "page": "{slug}",
     "<section_key>": {{
       "title":      "Short headline",
       "subtitle":   "Optional supporting line",
       "cta_primary": {{ "label": "Apply Now", "href": "#apply" }},
       "image_alt":  "Description of the image at this slot",
       "items": [
         {{ "title": "...", "description": "...", ... }}
       ]
     }},
     ...
   }}

   • Use one TOP-LEVEL key per section, named in snake_case derived from
     the section type (hero → "hero", open_roles → "open_roles", etc.).
   • Include EVERY string that appears in the JSX: titles, subtitles,
     button labels, alt text, FAQ Q&A pairs, list items, footer links.
   • Collections (gallery items, FAQ items, testimonials, roles, dishes)
     go under a "items" array with one object per entry.
   • image_alt fields MUST match the alt text from the IMAGES block above.
   • Buttons go as {{ "label": "...", "href": "#..." }} objects.

2. The N section .jsx files at {component_pages_dir}/  — each MUST:

   a) Import the content JSON:
        import content from "{content_import_path}";
        import {{ Editable }} from "@/lib/editable";

   b) Reference every visible string via `content.<section_key>.<field>`.
      FORBIDDEN:  <h1>Welcome to Acme Logistics</h1>
      REQUIRED:   <h1>{{content.hero.title}}</h1>

   c) Wrap every editable element with <Editable path="..." type="...">:

        <Editable path="hero.title" type="text">
          <h1 className="...">{{content.hero.title}}</h1>
        </Editable>

        <Editable path="hero.subtitle" type="text">
          <p className="...">{{content.hero.subtitle}}</p>
        </Editable>

        <a href={{content.hero.cta_primary.href}}>
          <Editable path="hero.cta_primary.label" type="text">
            <span>{{content.hero.cta_primary.label}}</span>
          </Editable>
        </a>

        <Editable path="hero.image_alt" type="text">
          <img src="https://images.unsplash.com/..." alt={{content.hero.image_alt}} />
        </Editable>

   d) Editable types — pick the right one:
        text       — plain string
        rich_text  — long copy that may contain inline formatting
        image      — <img>/<Image>: wrap so editor can swap src + alt
        button     — wrap the LABEL of an <a>/<button>; href stays in JSX
        color      — only for color tokens
        array      — wrap a list element (the parent) so editor can add/remove

   e) DO NOT wrap layout containers, decorative divs, or className strings
      with <Editable>. ONLY content the user would want to change.

3. Page composition at {page_file}
   - Imports each section component (no content imports here).
   - Exports a default function rendering them inside a <main>.
   - NO <Editable> wrappers in the composition file.

══ CRITICAL RULES ══
  - Every visible text node in section .jsx files MUST be a {{content.…}}
    expression. The ONLY string literals allowed in JSX text are:
    Tailwind class names, aria attributes, JSON keys, and import paths.
  - NEVER inline <h1>literal text</h1>, <p>literal</p>, label="literal" inside <Editable>.
  - The path attribute on <Editable> must match a real key in content.json.
"""
    else:
        content_block = ""

    return f"""GENERATE THE COMPLETE "{page_title}" PAGE FOR ROUTE {route}

PAGE PURPOSE
{page_purpose or '(general page for this route)'}

SECTIONS TO BUILD ({len(section_specs)} total) — generate each as its own .jsx file under {component_pages_dir}/:
{section_block}{images_block}{collections_block}{detail_hint_block}{content_block}

REQUIRED OUTPUT FILES
1. {page_file}
   - Imports each section component from {component_pages_dir.replace('src/', '@/')}
   - Exports the page composition (Next.js app router pattern)
   - Imports {{ siteConfig }} from '@/config/site' if it sets metadata
   - Renders sections inside a <main> wrapper in the order listed above

2. One .jsx file per section under {component_pages_dir}/ — exact names listed above{(f'''

3. One content JSON at {content_json_path} — see CONTENT / CODE SEPARATION
   block above for shape and rules.''') if _content_separation_enabled() else ''}

CRITICAL
  - Stay strictly inside the page's design (the anatomies above ARE the spec).
  - Every section reflects VISUAL_DNA visibly (motifs/textures/iconography/typography).
  - All section components must be DEFAULT EXPORTS named exactly as listed.
  - Page composition file does ONLY imports + a HomePage/AboutPage/etc default export.

Return JSON with the files array — that's it.
"""


async def generate_one_page(
    *,
    page: dict,
    visual_dna: dict,
    brand_name: str,
    tagline: str,
    domain: str,
    api_key: str,
    websocket: Any = None,
    foundation_imports: dict[str, str] | None = None,
    page_images: dict | None = None,
    data_model: Any = None,   # DataModel | None — Stage 4.5 output
) -> list[dict] | None:
    """Generate one complete page (composition + sections) in a single Claude call.

    Returns a list of {path, content} dicts on success, or None on failure
    (all attempts exhausted). Caller writes the files to disk.

    `page` shape:
      {
        "route": "/menu",      # or "path"
        "title": "Our Menu",
        "purpose": "Show seasonal menu with cultural narrative",
        "sections": [
          {"type": "hero",      "purpose": "Open with seasonal headline"},
          {"type": "menu",      "purpose": "Display dishes with prices"},
          {"type": "story",     "purpose": "Chef and farm sourcing"},
        ],
      }
    """
    from app.services.llm_json_client import call_claude_for_json

    route = (page.get("route") or page.get("path") or "/").strip()
    slug = _slug_from_route(route)
    raw_sections = page.get("sections") or []

    # Build section specs with per-page component names + anatomy from DNA
    section_specs: list[dict] = []
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        s_type = (s.get("type") or s.get("name") or "section").strip().lower()
        comp = _section_component_name(slug, s_type)
        section_specs.append({
            "type": s_type,
            "component": comp,
            "purpose": (s.get("purpose") or s.get("description") or "").strip(),
            "anatomy": _section_anatomy_for(visual_dna, s_type),
        })

    if not section_specs:
        logger.warning("page_generator: page %s has no sections — skipping", route)
        return None

    foundation = foundation_imports or {
        "globals.css":          "Theme palette + Google Fonts (do NOT regenerate)",
        "design-system.js":     "ds tokens for spacing/typography (import { ds } from '@/lib/design-system')",
        "site.js":              "{ siteConfig } from '@/config/site' (brand name, tagline, URL)",
        "navigation.js":        "{ mainNav, footerNav } from '@/config/navigation'",
        "MarketingHeader.jsx":  "Wraps every page (rendered by layout.jsx)",
        "MarketingFooter.jsx":  "Wraps every page (rendered by layout.jsx)",
    }

    sys_p = _build_system_prompt(
        brand_name=brand_name, tagline=tagline, domain=domain,
        visual_dna=visual_dna, foundation_imports=foundation,
    )
    usr_p = _build_user_prompt(
        page=page, slug=slug,
        section_specs=section_specs, visual_dna=visual_dna,
        page_images=page_images,
        data_model=data_model,
    )

    # Log prompt size so we can diagnose token-budget issues.
    # ~4 chars per token rough estimate. If sys + usr > 100K chars (~25K tokens)
    # we're eating significant context budget that competes with output room.
    logger.info(
        "page_generator: page %s prompt sizes — sys=%d chars (~%dk tok), usr=%d chars (~%dk tok), "
        "sections=%d, images=%s, has_data_model=%s",
        route,
        len(sys_p), len(sys_p) // 4000,
        len(usr_p), len(usr_p) // 4000,
        len(section_specs),
        len((page_images or {})) if isinstance(page_images, dict) else "?",
        bool(data_model),
    )

    last_failure = "unknown"
    for attempt in range(1, _PAGE_MAX_ATTEMPTS + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_PAGE_MAX_TOKENS,
                ),
                timeout=_PAGE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            last_failure = f"timeout after {_PAGE_TIMEOUT_S}s"
            logger.warning(
                "page_generator: page %s timeout on attempt %d/%d",
                route, attempt, _PAGE_MAX_ATTEMPTS,
            )
            continue
        except Exception as exc:
            last_failure = f"exception: {exc}"
            logger.warning(
                "page_generator: page %s threw on attempt %d/%d — %s",
                route, attempt, _PAGE_MAX_ATTEMPTS, exc,
            )
            continue

        if not result or "files" not in result:
            last_failure = "no files key in response"
            continue

        files = result.get("files") or []
        if not files:
            last_failure = "empty files array"
            continue

        # Validate: must contain the page.js composition + at least one section
        # When CONTENT_SEPARATION_ENABLED is on, also require the content JSON file.
        valid_files: list[dict] = []
        saw_page_js = False
        saw_content_json = False
        expected_content_path = f"src/content/pages/{slug}.json"
        for f in files:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            content = f.get("content")
            # Claude sometimes returns the `content` field as a dict (when its
            # tool_use schema lets it emit structured JSON directly) rather
            # than a serialized string. json.loads() on a dict raises a
            # TypeError that the outer try/except json.JSONDecodeError below
            # does NOT catch — the exception then propagates up and kills
            # the whole page (the JSON object must be str, bytes or bytearray,
            # not dict error). Coerce to string here so both code paths work.
            if isinstance(content, (dict, list)):
                try:
                    content = json.dumps(content, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    content = ""
            elif content is None:
                content = ""
            elif not isinstance(content, str):
                content = str(content)
            if not path or not content:
                continue
            if path.endswith("page.js") or path.endswith("page.jsx"):
                saw_page_js = True
            if path.lstrip("/") == expected_content_path:
                # Validate the JSON parses — otherwise it's just a wall of text.
                # Now safe: content is guaranteed to be a string by the coercion above.
                try:
                    json.loads(content)
                    saw_content_json = True
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "page_generator: page %s content JSON failed to parse — %s",
                        route, exc,
                    )
                    continue
            valid_files.append({"path": path, "content": content})

        if not saw_page_js or len(valid_files) < 2:
            last_failure = f"missing composition file or section files (got {len(valid_files)})"
            logger.warning(
                "page_generator: page %s attempt %d/%d — %s",
                route, attempt, _PAGE_MAX_ATTEMPTS, last_failure,
            )
            continue

        if _content_separation_enabled() and not saw_content_json:
            last_failure = f"missing content JSON at {expected_content_path}"
            logger.warning(
                "page_generator: page %s attempt %d/%d — %s",
                route, attempt, _PAGE_MAX_ATTEMPTS, last_failure,
            )
            continue

        logger.info(
            "page_generator: page %s ok on attempt %d — %d files generated",
            route, attempt, len(valid_files),
        )
        return valid_files

    logger.error(
        "page_generator: page %s FAILED after %d attempts — last_failure=%s",
        route, _PAGE_MAX_ATTEMPTS, last_failure,
    )
    return None


# ── Per-section codegen (mirrors landing pipeline architecture) ───────

# Each section gets its own focused Claude call. Total spend goes up
# ~30-50% but per-section quality is much higher because:
#   • Claude isn't juggling 4-6 unrelated sections in one response
#   • Each call gets full anatomy + visual_dna + research context aimed
#     at one section type
#   • Failures are isolated — one bad section ≠ broken page
_SECTION_INNER_CONCURRENCY = 4  # parallel sections within a single page


async def generate_page_per_section(
    *,
    page: dict,
    visual_dna: dict,
    brand_name: str,
    tagline: str,
    domain: str,
    api_key: str,
    websocket: Any = None,
    foundation_imports: dict[str, str] | None = None,
    page_images: dict | None = None,
    data_model: Any = None,
    design_tokens: dict | None = None,
    personality: dict | None = None,
    voice_context: dict | None = None,
    design_system: dict | None = None,
) -> list[dict] | None:
    """Per-section codegen — generates one Claude call per section in
    parallel, then assembles a deterministic composition file.

    Drop-in replacement for `generate_one_page` that produces the same
    return shape: list of {path, content} for the composition file plus
    each section component. No content/pages/<slug>.json — sections
    embed their copy directly (matches landing pattern).

    Reuses `landing_section_codegen._generate_one_section` so per-section
    prompts, fallbacks, and validation are identical to the landing
    pipeline's proven path.
    """
    from app.services.landing_section_codegen import _generate_one_section

    route = (page.get("route") or page.get("path") or "/").strip()
    slug = _slug_from_route(route)
    raw_sections = page.get("sections") or []

    # Normalize sections with stable IDs (so component names stay unique
    # across pages — HomeHero, AboutHero, etc.).
    prepared: list[dict[str, Any]] = []
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        s_type = (s.get("type") or s.get("name") or "section").strip().lower()
        prepared.append({
            **s,
            "id": s.get("id") or f"{slug}_{s_type}",
            "type": s_type,
        })

    if not prepared:
        logger.warning("page_generator(per-section): page %s has no sections", route)
        return None

    label = f"page_generator(per-section)[{slug or 'home'}]"
    logger.info(
        "%s: starting — %d sections (parallel, sem=%d)",
        label, len(prepared), _SECTION_INNER_CONCURRENCY,
    )

    # Visual DNA breakouts (defensive: missing fields → empty)
    motif = (visual_dna.get("motif") or visual_dna.get("primary_motif") or "modern").strip()
    palette = visual_dna.get("palette") or {}
    typography = visual_dna.get("typography") or {}
    personality_dict = personality or visual_dna.get("personality") or {}
    references = visual_dna.get("references") or []
    design_tokens_dict = design_tokens or visual_dna.get("design_tokens") or {}
    # Project-derived design_system from website_pipeline. Without this,
    # the section codegen prompt renders every enum at its hard-coded
    # default (subtle/rounded/elevated/natural/balanced) — that's why
    # every website used to feel identical regardless of brand/category.
    design_system_dict = dict(design_system or {})

    # Page-specific section anatomies (Option C — page_anatomy_refiner output)
    # take precedence over the global ones from visual_dna. The refiner
    # attaches `page["section_anatomies"]` per page; missing entries fall
    # back to the global map so partial refinement is safe.
    global_anatomies = dict(visual_dna.get("section_anatomies") or {})
    page_anatomies = dict(page.get("section_anatomies") or {})
    effective_anatomies = {**global_anatomies, **page_anatomies}
    if page_anatomies:
        logger.info(
            "%s: using %d page-refined anatomies (overriding global)",
            label, len(page_anatomies),
        )
    # Inject the effective anatomies into a shallow visual_dna copy so the
    # downstream section prompt builder reads the page-specific versions.
    visual_dna_for_page = {**visual_dna, "section_anatomies": effective_anatomies}

    # Image flow: bind_page_images returns {section_type: [{url, alt, ...}]}
    # keyed by section type (with _2/_3 suffixes for repeats). The landing
    # section prompt instructs Claude that `section.images` is an array of
    # URL STRINGS, and the runtime landing.json the components import has
    # the same shape — so we flatten dicts → strings here. Without this
    # Claude sees `[{url: "..."}]` in the SECTION SPEC, hardcodes that
    # shape, and renders `<Image src={dict}>` which produces a broken icon.
    if page_images and isinstance(page_images, dict):
        type_seen: dict[str, int] = {}
        for s in prepared:
            s_type = (s.get("type") or "").strip().lower()
            type_seen[s_type] = type_seen.get(s_type, 0) + 1
            key = s_type if type_seen[s_type] == 1 else f"{s_type}_{type_seen[s_type]}"
            raw_imgs = page_images.get(key) or page_images.get(s_type) or []
            urls = [
                (img.get("url") or "").strip()
                for img in raw_imgs
                if isinstance(img, dict) and (img.get("url") or "").strip()
            ]
            if urls:
                s["images"] = urls
                s["image_alts"] = [
                    (img.get("alt") or "").strip()
                    for img in raw_imgs if isinstance(img, dict)
                ]

    sem = asyncio.Semaphore(_SECTION_INNER_CONCURRENCY)

    async def _one(idx: int, section: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            try:
                # Prev + next only, with the same fields landing uses
                # (id/type/layout_hint/archetype). Passing every other
                # section as before flooded the anti-monotony block with
                # 6-10 siblings and diluted its effect — landing keeps it
                # to 2 because the rule is "look DIFFERENT from neighbors,"
                # not "look different from the whole page."
                siblings: list[dict[str, Any]] = []
                if idx - 1 >= 0:
                    siblings.append(prepared[idx - 1])
                if idx + 1 < len(prepared):
                    siblings.append(prepared[idx + 1])
                result = await _generate_one_section(
                    section=section,
                    siblings=siblings,
                    brand_name=brand_name,
                    motif=motif,
                    palette=palette,
                    typography=typography,
                    design_system=design_system_dict,
                    personality=personality_dict,
                    references=references,
                    design_tokens=design_tokens_dict,
                    section_index=idx,
                    section_count=len(prepared),
                    api_key=api_key,
                    websocket=websocket,
                    voice_context=voice_context,
                    visual_dna=visual_dna_for_page,
                )
            except Exception as exc:
                # _generate_one_section already falls back internally after
                # its retries, so reaching here means prompt-building itself
                # raised (deterministic — a page-level retry won't fix it).
                # Write the same deterministic fallback skeleton landing uses
                # instead of silently dropping the section from the page.
                logger.warning(
                    "%s: section %d (%s) threw — writing fallback — %s",
                    label, idx, section.get("type"), exc,
                )
                try:
                    from app.services.landing_section_codegen import (
                        _component_name, _fallback_section_component,
                        _section_filename,
                    )
                    fb_filename = _section_filename(section)
                    fb = _fallback_section_component(
                        section,
                        _component_name(fb_filename),
                        f"src/components/sections/{fb_filename}",
                    )
                    if fb and fb.get("content"):
                        return {
                            "path": f"src/components/pages/{slug}/{fb_filename}",
                            "content": fb["content"],
                        }
                except Exception as fb_exc:
                    logger.error(
                        "%s: section %d fallback also failed — %s",
                        label, idx, fb_exc,
                    )
                return None
            if not result:
                return None
            # Rewrite section path from landing's `src/components/sections/`
            # to website's per-page structure: `src/components/pages/<slug>/`
            old_path = result.get("path") or ""
            content = result.get("content") or ""
            if not old_path or not content:
                return None
            filename = os.path.basename(old_path)
            new_path = f"src/components/pages/{slug}/{filename}"
            return {"path": new_path, "content": content}

    section_results = await asyncio.gather(
        *[_one(i, s) for i, s in enumerate(prepared)],
        return_exceptions=False,
    )
    section_files = [f for f in section_results if f]

    if not section_files:
        logger.error("%s: ALL %d sections failed", label, len(prepared))
        return None

    failed_count = len(prepared) - len(section_files)
    if failed_count > 0:
        logger.warning(
            "%s: %d/%d sections succeeded (%d failed)",
            label, len(section_files), len(prepared), failed_count,
        )

    # Composition file — deterministically built from successful sections
    composition = _build_page_composition(page, slug, section_files, brand_name)
    if composition:
        return [composition] + section_files

    return section_files


def _build_page_composition(
    page: dict,
    slug: str,
    section_files: list[dict],
    brand_name: str,
) -> dict | None:
    """Build the page.js composition file that imports and renders
    each section component."""
    route = (page.get("route") or page.get("path") or "/").strip()
    if route in ("", "/"):
        route_path = "src/app/page.js"
    else:
        rel = route.lstrip("/")
        # Skip dynamic routes — they're emitted by detail-route builder.
        if "[" in rel or "]" in rel:
            return None
        route_path = f"src/app/{rel}/page.js"

    imports: list[str] = []
    body: list[str] = []
    for f in section_files:
        basename = os.path.basename(f["path"])
        # Strip .jsx / .js
        component = re.sub(r"\.(jsx|js)$", "", basename)
        if not _VALID_COMPONENT.match(component):
            continue
        # Path relative to src/ — Next.js @/ alias
        rel_import = f["path"].removeprefix("src/")
        rel_import = re.sub(r"\.(jsx|js)$", "", rel_import)
        imports.append(f'import {component} from "@/{rel_import}";')
        body.append(f"      <{component} />")

    if not imports:
        return None

    title = (page.get("title") or "Page").strip()
    title_line = (
        f"`{title} — ${{siteConfig.name}}`"
        if route not in ("", "/")
        else "`${siteConfig.name} — ${siteConfig.tagline}`"
    )

    content = (
        'import { siteConfig } from "@/config/site";\n'
        + "\n".join(imports) + "\n"
        '\n'
        'export const metadata = {\n'
        f'  title: {title_line},\n'
        '};\n'
        '\n'
        'export default function Page() {\n'
        '  return (\n'
        '    <main>\n'
        + "\n".join(body) + "\n"
        '    </main>\n'
        '  );\n'
        '}\n'
    )

    return {"path": route_path, "content": content}


def plan_section_components_for_page(
    page: dict,
    slug: str | None = None,
) -> list[str]:
    """Helper for callers (e.g. homepage builder) that need to know what
    component names a page WOULD generate, without running Claude.

    Returns the list of per-page component names in render order.
    """
    s = slug or _slug_from_route(page.get("route") or page.get("path") or "/")
    return [
        _section_component_name(s, sec.get("type") or sec.get("name") or "section")
        for sec in (page.get("sections") or [])
        if isinstance(sec, dict)
    ]
