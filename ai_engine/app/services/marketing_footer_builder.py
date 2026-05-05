"""Deterministic src/components/layout/MarketingFooter.jsx builder.

Emits a multi-column footer with:
  - Brand column: logo + tagline blurb + small social-icon row
  - 2-3 link columns sourced from project_schema.navigation
    (a single "main" group is auto-split into 2 columns when it has
    >= 4 items so the footer never collapses to a single sad column)
  - Static "Company" column when the schema doesn't supply one,
    so the bottom of the page always feels structured even for
    minimal landing pages
  - Bottom bar: copyright + "all rights reserved"

Why deterministic:
  - Removes the Footer from Phase 1's output budget.
  - Eliminates LLM bugs around inline SVG fallbacks for icons that
    actually ship in lucide-react (Instagram, Facebook, Twitter,
    Linkedin), and pulling fields from siteConfig that
    site_config_builder doesn't emit.

Pure module — no I/O, no LLM, no async.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────
#  Helpers
# ───────────────────────────────────────────────────────────────
def _coerce(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _humanize_group_label(raw: str) -> str:
    """Translate schema group names to footer-friendly labels.

    Default landing schemas use ``group: "main"`` for the single nav group
    derived from sections. Rendering "main" verbatim in a footer column
    looks half-finished — turn it into ``"Explore"`` instead. Other group
    names are title-cased so ``"open_roles"`` becomes ``"Open Roles"``.
    """
    if not raw:
        return "Explore"
    low = raw.strip().lower()
    aliases = {
        "main": "Explore",
        "primary": "Explore",
        "site": "Explore",
        "navigation": "Explore",
        "nav": "Explore",
    }
    if low in aliases:
        return aliases[low]
    cleaned = raw.strip().replace("_", " ").replace("-", " ")
    return " ".join(w.capitalize() for w in cleaned.split())


def _split_links_to_columns(
    links: list[dict],
    *,
    target_columns: int = 2,
    min_per_col: int = 2,
) -> list[list[dict]]:
    """Split a flat list of links into N near-equal columns.

    Below ``min_per_col`` total items we don't bother splitting — a
    single column with two items reads better than two columns with
    one each.
    """
    if len(links) < min_per_col * target_columns:
        return [links]
    per = max(1, (len(links) + target_columns - 1) // target_columns)
    return [links[i:i + per] for i in range(0, len(links), per)]


def _build_columns(navigation: list[dict]) -> list[dict]:
    """Return up to 3 footer columns from the schema's grouped nav.

    Behavior:
      - Multiple distinct groups → first 3 groups become columns
        (capped at 6 links each).
      - Single group with >= 4 items → split into two columns
        ("Explore" + "More") so the footer fills out.
      - Single group with < 4 items → one column, padded with a
        static "Company" column so the layout still has structure.
    """
    cleaned: list[dict] = []
    for grp in navigation or []:
        if not isinstance(grp, dict):
            continue
        items = grp.get("items") or []
        clean_links: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            label = _coerce(it.get("label"))
            href = _coerce(it.get("path") or it.get("href"))
            if label and href:
                clean_links.append({"label": label, "href": href})
        if clean_links:
            cleaned.append({
                "group": _coerce(grp.get("group")),
                "links": clean_links,
            })

    if not cleaned:
        return []

    # Multi-group schema → render first 3 as-is.
    if len(cleaned) >= 2:
        return [
            {
                "title": _humanize_group_label(g["group"]),
                "links": g["links"][:6],
            }
            for g in cleaned[:3]
        ]

    # Single group → maybe split into two columns to avoid the
    # "one tiny column" footer.
    only = cleaned[0]
    chunks = _split_links_to_columns(only["links"], target_columns=2)
    if len(chunks) == 1:
        return [{
            "title": _humanize_group_label(only["group"]),
            "links": chunks[0][:6],
        }]
    base_title = _humanize_group_label(only["group"])
    titles = [base_title, "More"] if base_title != "More" else ["Explore", "More"]
    return [
        {"title": titles[i], "links": chunks[i][:6]}
        for i in range(min(2, len(chunks)))
    ]


# Static "Company" column appended when the schema only gives us
# one nav column. Keeps the footer feeling "real" even on the
# simplest landing pages without forcing the LLM to invent links.
_DEFAULT_COMPANY_COLUMN = {
    "title": "Company",
    "links": [
        {"label": "About", "href": "#about"},
        {"label": "Contact", "href": "#contact"},
        {"label": "Privacy", "href": "#privacy"},
        {"label": "Terms", "href": "#terms"},
    ],
}


# ───────────────────────────────────────────────────────────────
#  Renderer
# ───────────────────────────────────────────────────────────────
def build_marketing_footer_jsx(
    *,
    brand_name: str,
    description: str,
    navigation: list[dict],
    copyright_year_var: str = "{new Date().getFullYear()}",
) -> str:
    """Render MarketingFooter.jsx as a JSX source string.

    Inputs:
      - brand_name: project_schema.brand.name (or first 30 chars of
        description as final fallback)
      - description: project_schema.brand.description or tagline (used
        as the brand-column blurb; truncated downstream)
      - navigation: project_schema.navigation, grouped shape
      - copyright_year_var: the JSX expression to splice into the © line.
        Default uses runtime new Date().getFullYear(); pass a literal
        year string for snapshot tests.
    """
    brand_safe = _coerce(brand_name) or "Brand"
    blurb = _coerce(description)
    if len(blurb) > 200:
        blurb = blurb[:200].rsplit(" ", 1)[0].rstrip(",.;:") + "…"

    columns = _build_columns(navigation)
    # Always show ≥ 2 link columns. If we only got one from the schema,
    # append the static Company column so the footer doesn't look empty.
    if len(columns) < 2:
        columns = columns + [_DEFAULT_COMPANY_COLUMN]
    columns = columns[:3]

    def _jsx_text(s: str) -> str:
        return s.replace("{", "&#123;").replace("}", "&#125;")

    brand_jsx = _jsx_text(brand_safe)
    blurb_jsx = _jsx_text(blurb) if blurb else ""

    col_blocks: list[str] = []
    for col in columns:
        link_lines = []
        for link in col["links"]:
            href_attr = json.dumps(link["href"], ensure_ascii=False)
            label_text = _jsx_text(link["label"])
            link_lines.append(
                f'              <li>\n'
                f'                <Link href={{{href_attr}}} className="text-sm text-muted-foreground hover:text-foreground transition-colors">\n'
                f'                  {label_text}\n'
                f'                </Link>\n'
                f'              </li>'
            )
        title_text = _jsx_text(col["title"])
        col_blocks.append(
            '          <div>\n'
            f'            <h3 className="text-sm font-semibold text-foreground tracking-wide">{title_text}</h3>\n'
            '            <ul className="mt-4 space-y-3">\n'
            + "\n".join(link_lines)
            + '\n            </ul>\n'
            '          </div>'
        )
    nav_section = "\n\n".join(col_blocks)

    blurb_block = (
        f'            <p className="text-sm leading-relaxed text-muted-foreground max-w-xs">\n'
        f'              {blurb_jsx}\n'
        f'            </p>\n'
        if blurb_jsx else ""
    )

    # Brand column = logo + blurb + social row. We render the social
    # row regardless — siteConfig.socials may be empty, in which case
    # the JSX simply renders nothing (Array.filter on null entries).
    grid_cols_lg = 1 + len(columns)  # brand + N nav cols
    grid_cols_lg = min(grid_cols_lg, 4)
    grid_class_lg = f"lg:grid-cols-{grid_cols_lg}"

    return f"""import Link from "next/link";
import {{ Facebook, Instagram, Linkedin, Twitter, Youtube, Github }} from "lucide-react";
import {{ siteConfig }} from "@/config/site";

const SOCIAL_ICONS = {{
  facebook: Facebook,
  instagram: Instagram,
  linkedin: Linkedin,
  twitter: Twitter,
  x: Twitter,
  youtube: Youtube,
  github: Github,
}};

function getSocials() {{
  const raw = (siteConfig && siteConfig.socials) || {{}};
  return Object.entries(raw)
    .map(([key, href]) => {{
      if (!href) return null;
      const Icon = SOCIAL_ICONS[key.toLowerCase()];
      if (!Icon) return null;
      return {{ key, href, Icon }};
    }})
    .filter(Boolean);
}}

export function MarketingFooter() {{
  const socials = getSocials();
  return (
    <footer className="border-t border-border bg-background">
      <div className="container mx-auto px-4 py-16 sm:px-6 lg:px-8">
        <div className="grid grid-cols-2 gap-10 sm:grid-cols-2 {grid_class_lg}">
          <div className="col-span-2 space-y-5 sm:col-span-2 lg:col-span-1">
            <Link href="/" className="inline-flex items-center gap-2 font-bold tracking-tight text-lg text-foreground">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-semibold">
                {{(siteConfig && siteConfig.logoText && siteConfig.logoText[0]) || "{brand_jsx[:1] or 'B'}"}}
              </div>
              {brand_jsx}
            </Link>
{blurb_block}            {{socials.length > 0 && (
              <div className="flex items-center gap-3 pt-1">
                {{socials.map(({{ key, href, Icon }}) => (
                  <a
                    key={{key}}
                    href={{href}}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={{key}}
                    className="flex h-9 w-9 items-center justify-center rounded-md border border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground transition-colors"
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </a>
                ))}}
              </div>
            )}}
          </div>

{nav_section}
        </div>

        <div className="mt-14 border-t border-border pt-6 flex flex-col items-center justify-between gap-3 sm:flex-row">
          <p className="text-xs text-muted-foreground">
            &copy; {copyright_year_var} {brand_jsx}. All rights reserved.
          </p>
          <p className="text-xs text-muted-foreground">
            Made with care.
          </p>
        </div>
      </div>
    </footer>
  );
}}

export default MarketingFooter;
"""
