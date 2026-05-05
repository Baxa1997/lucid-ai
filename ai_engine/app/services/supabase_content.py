"""Project schema → customer Supabase rows.

Reads ``project_schema`` (the canonical dict produced by step 1) and writes
its parts into the per-customer Supabase tables defined in
``supabase/migrations/per_project/001_content_tables.sql``.

Mapping (one place to look when fields rename):

    project_schema["brand"]            → gen_site_config.brand          (single row)
    project_schema["theme"]            → gen_site_config.theme
    project_schema["design"]           → gen_site_config.design
    project_schema["archetype"]        → gen_site_config.archetype
    project_schema["domain_kind"]      → gen_site_config.domain_kind
    project_schema["status_badges"]    → gen_site_config.status_badges
    project_schema["design_system"]    → gen_site_config.design_system
    project_schema["api_config"]       → gen_site_config.api_config

    project_schema["navigation"]       → gen_navigation.* rows
                                          (kind="main", one row per group)
    Optional flat footerNav            → gen_navigation rows w/ kind="footer"

    project_schema["pages"]            → gen_page rows (one per page)
                                          home / "/" gets a synthetic row
                                          when project has sections only
    page.sections (or schema.sections  → gen_section rows
      for landing-only sites)

    project_schema["entities"]         → gen_entity rows
    project_schema["mock_db"]          → gen_entity_row rows (seed data)

Talks to the customer's Supabase via its **service_role key** so RLS is
bypassed (writes don't need a user JWT). Calls are made with the official
``supabase`` async client built per-project — never reuses Lucid's central
``managed_admin_client`` (different DB).

Pure side-effects on remote DB; no return value beyond a summary dict for
logging / WS progress events.
"""
from __future__ import annotations

import logging
from typing import Any

from supabase import create_async_client, AsyncClient

logger = logging.getLogger(__name__)


# ── Tiny shape helpers ────────────────────────────────────────────────────

def _coerce_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _coerce_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _section_type_from_entry(entry: Any) -> str:
    """Sections in the schema can be:
       - a dict with `type` key
       - a string (just the section type name)
    Normalize to a non-empty string."""
    if isinstance(entry, dict):
        t = entry.get("type") or entry.get("kind") or "custom"
        return str(t).strip() or "custom"
    if isinstance(entry, str):
        return entry.strip() or "custom"
    return "custom"


def _section_props_from_entry(entry: Any) -> dict:
    """Sections may carry copy directly (headline, subheadline, content) or
    nest it under a `props`/`content` key. Whatever's there gets passed
    through verbatim — the React component decides which keys it reads."""
    if isinstance(entry, dict):
        props = _coerce_dict(entry.get("props"))
        for k in ("headline", "subheadline", "content", "items", "ctaText", "ctaHref",
                  "imageHint", "animation", "description"):
            if k in entry and k not in props:
                props[k] = entry[k]
        return props
    return {}


# ── Builders that turn schema into row dicts (no DB calls) ────────────────

def _build_site_config_row(schema: dict) -> dict:
    """Single row for gen_site_config. All jsonb fields default to empty
    so the row is valid even when an early-stage schema is sparse."""
    return {
        "brand":         _coerce_dict(schema.get("brand")),
        "theme":         _coerce_dict(schema.get("theme")),
        "design":        _coerce_dict(schema.get("design")),
        "archetype":     str(schema.get("archetype") or "") or None,
        "domain_kind":   str(schema.get("domain_kind") or "") or None,
        "status_badges": _coerce_dict(schema.get("status_badges")),
        "design_system": _coerce_dict(schema.get("design_system")),
        "api_config":    _coerce_dict(schema.get("api_config")),
    }


def _build_navigation_rows(schema: dict) -> list[dict]:
    """One row per nav group. The schema's `navigation` is already grouped
    [{group, items}]; we treat group_name verbatim. Header/sidebar comes
    from `navigation`; footer rows are synthesized from the same items
    (clients can override later via CMS).

    For admin archetypes the same `navigation` array is the sidebar; we
    tag those rows kind='sidebar'. For consumer archetypes it's kind='main'.
    """
    archetype = str(schema.get("archetype") or "").lower()
    is_admin = archetype in {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}
    primary_kind = "sidebar" if is_admin else "main"

    rows: list[dict] = []
    for idx, group in enumerate(_coerce_list(schema.get("navigation"))):
        if not isinstance(group, dict):
            continue
        items = _coerce_list(group.get("items"))
        if not items:
            continue
        normalised_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            label = (it.get("label") or "").strip()
            href = (it.get("path") or it.get("href") or "").strip()
            if not label or not href:
                continue
            entry = {"label": label, "href": href}
            if it.get("icon"):
                entry["icon"] = str(it["icon"]).strip()
            if it.get("badge"):
                entry["badge"] = str(it["badge"]).strip()
            normalised_items.append(entry)
        if not normalised_items:
            continue
        rows.append({
            "kind":       primary_kind,
            "group_name": (group.get("group") or "main") or "main",
            "position":   idx,
            "items":      normalised_items,
        })

    # Mirror the same nav as a footer block for consumer sites — the
    # generated MarketingFooter consumes it. Admin sites don't have a
    # marketing footer, so skip there.
    if not is_admin and rows:
        for r in list(rows):
            rows.append({
                "kind":       "footer",
                "group_name": r["group_name"],
                "position":   r["position"],
                "items":      r["items"],
            })

    return rows


def _synthetic_home_for_landing(schema: dict) -> dict:
    """Landing archetypes have `pages: []`; the home page is implied. We
    emit a synthetic gen_page row so gen_section.page_id has a target."""
    brand_name = (schema.get("brand") or {}).get("name") or "Home"
    return {
        "path":      "/",
        "title":     str(brand_name),
        "component": "HomePage",
        "type":      "landing",
        "metadata":  {},
        "position":  0,
    }


def _build_page_rows(schema: dict) -> list[dict]:
    """Convert schema.pages → gen_page rows. For landing sites with no
    pages, return one synthetic home row. Position is dictated by array
    order (matches Phase 2 / route generation)."""
    pages = _coerce_list(schema.get("pages"))
    if not pages:
        return [_synthetic_home_for_landing(schema)]
    rows: list[dict] = []
    for idx, p in enumerate(pages):
        if not isinstance(p, dict):
            continue
        path = (p.get("path") or "").strip() or "/"
        title = (p.get("title") or "").strip()
        rows.append({
            "path":      path,
            "title":     title or path,
            "component": (p.get("component") or "").strip() or None,
            "type":      (p.get("type") or "custom").strip() or "custom",
            "metadata":  _coerce_dict(p.get("metadata")),
            "position":  idx,
        })
    return rows


def _build_section_rows(schema: dict, page_id_by_path: dict[str, str]) -> list[dict]:
    """Sections live in two different places depending on archetype:

    - Landing (`sections`): top-level array on schema, all belong to home.
    - Pages (`pages[].sections`): per-page array, each section attaches
      to its parent page.

    For admin archetypes there are usually no sections (CRUD pages have
    no marketing-style content blocks).
    """
    out: list[dict] = []

    archetype = str(schema.get("archetype") or "").lower()
    if archetype == "single_page_landing":
        home_id = page_id_by_path.get("/")
        if home_id:
            for idx, sec in enumerate(_coerce_list(schema.get("sections"))):
                out.append({
                    "page_id":  home_id,
                    "position": idx,
                    "type":     _section_type_from_entry(sec),
                    "props":    _section_props_from_entry(sec),
                    "visible":  True,
                })
        return out

    # Consumer / blog / marketplace — sections live on each page.
    for p in _coerce_list(schema.get("pages")):
        if not isinstance(p, dict):
            continue
        path = (p.get("path") or "").strip()
        page_id = page_id_by_path.get(path)
        if not page_id:
            continue
        for idx, sec in enumerate(_coerce_list(p.get("sections"))):
            out.append({
                "page_id":  page_id,
                "position": idx,
                "type":     _section_type_from_entry(sec),
                "props":    _section_props_from_entry(sec),
                "visible":  True,
            })
    return out


def _build_entity_rows_and_data(schema: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """gen_entity row per schema.entities entry, plus a slug→[rows] map of
    seed data pulled from schema.mock_db. We can't insert gen_entity_row
    rows until we know each entity's id, so the caller resolves that."""
    entities: list[dict] = []
    seed_by_slug: dict[str, list[dict]] = {}
    mock_db = _coerce_dict(schema.get("mock_db"))
    for ent in _coerce_list(schema.get("entities")):
        if not isinstance(ent, dict):
            continue
        slug = (ent.get("slug") or ent.get("name") or "").strip().lower().replace(" ", "_")
        if not slug:
            continue
        entities.append({
            "name":   (ent.get("name") or slug.replace("_", " ").title()).strip(),
            "slug":   slug,
            "fields": _coerce_list(ent.get("fields")),
        })
        rows = mock_db.get(slug) or mock_db.get(ent.get("slug") or "")
        if isinstance(rows, list):
            seed_by_slug[slug] = [{"data": r} for r in rows if isinstance(r, dict)]
    return entities, seed_by_slug


# ── DB writers ────────────────────────────────────────────────────────────

async def _client_for(supabase_url: str, service_key: str) -> AsyncClient:
    """One async client per persist call. Cheap to construct; cheaper than
    holding pooled state across pipeline steps that may be hours apart."""
    if not supabase_url or not service_key:
        raise ValueError("supabase_content: supabase_url + service_key both required")
    return await create_async_client(supabase_url, service_key)


async def _insert_returning_ids(
    client: AsyncClient, table: str, rows: list[dict]
) -> list[str]:
    """Insert rows in one batch, return the generated ids in input order.
    Supabase inserts preserve order, so we can zip them with the originals."""
    if not rows:
        return []
    res = await client.table(table).insert(rows).execute()
    data = getattr(res, "data", None) or []
    if len(data) != len(rows):
        raise RuntimeError(
            f"supabase_content: expected {len(rows)} {table} rows back, got {len(data)}"
        )
    return [str(r["id"]) for r in data if "id" in r]


# ── Public entrypoint ─────────────────────────────────────────────────────

async def persist_schema_to_supabase(
    *, project_schema: dict, supabase_url: str, service_key: str,
) -> dict:
    """Mirror project_schema into the customer's content tables. Returns a
    summary dict {site_config: 1, navigation: N, page: N, section: N,
    entity: N, entity_row: N} for logging / WS progress.

    Idempotent assumption: caller guarantees the gen_* tables are EMPTY.
    Step 3 only runs this on a freshly provisioned project right after
    apply_per_project_migrations(). Later edits go through the CMS
    layer (step 8), not through this function.
    """
    client = await _client_for(supabase_url, service_key)

    summary = {"site_config": 0, "navigation": 0, "page": 0, "section": 0, "entity": 0, "entity_row": 0}

    # ── site_config (single row) ─────────────────────────────────────────
    site_row = _build_site_config_row(project_schema)
    res = await client.table("gen_site_config").insert(site_row).execute()
    if not (getattr(res, "data", None) or []):
        raise RuntimeError("supabase_content: gen_site_config insert returned no rows")
    summary["site_config"] = 1

    # ── navigation (one row per group; main + footer for consumer) ───────
    nav_rows = _build_navigation_rows(project_schema)
    if nav_rows:
        res = await client.table("gen_navigation").insert(nav_rows).execute()
        summary["navigation"] = len(getattr(res, "data", None) or [])

    # ── pages (then sections, indexed by page_id) ────────────────────────
    page_rows = _build_page_rows(project_schema)
    if page_rows:
        res = await client.table("gen_page").insert(page_rows).execute()
        page_data = getattr(res, "data", None) or []
        if len(page_data) != len(page_rows):
            raise RuntimeError(
                f"supabase_content: expected {len(page_rows)} gen_page rows back, "
                f"got {len(page_data)}"
            )
        summary["page"] = len(page_data)
        page_id_by_path: dict[str, str] = {}
        for inserted, original in zip(page_data, page_rows):
            page_id_by_path[original["path"]] = str(inserted["id"])

        section_rows = _build_section_rows(project_schema, page_id_by_path)
        if section_rows:
            res = await client.table("gen_section").insert(section_rows).execute()
            summary["section"] = len(getattr(res, "data", None) or [])

    # ── entities + their seed rows ───────────────────────────────────────
    entity_rows, seed_by_slug = _build_entity_rows_and_data(project_schema)
    if entity_rows:
        res = await client.table("gen_entity").insert(entity_rows).execute()
        ent_data = getattr(res, "data", None) or []
        summary["entity"] = len(ent_data)

        # Map slug → id, then bulk-insert the entity_row seed.
        id_by_slug: dict[str, str] = {}
        for inserted, original in zip(ent_data, entity_rows):
            id_by_slug[original["slug"]] = str(inserted["id"])

        all_seed: list[dict] = []
        for slug, rows in seed_by_slug.items():
            ent_id = id_by_slug.get(slug)
            if not ent_id:
                continue
            for r in rows:
                all_seed.append({"entity_id": ent_id, "data": r["data"]})
        if all_seed:
            res = await client.table("gen_entity_row").insert(all_seed).execute()
            summary["entity_row"] = len(getattr(res, "data", None) or [])

    logger.info("Schema → Supabase persist summary: %s", summary)
    return summary
