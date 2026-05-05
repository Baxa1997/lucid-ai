"""Step-3 end-to-end verification: per-project content backend.

Two test layers:

  --offline (default)  → no network calls. Verifies the schema → row
                         transformers (`_build_*` helpers) produce the
                         right shapes for every archetype.

  --live               → real Supabase project lifecycle:
                            provision → migrate → persist → verify rows
                            → cleanup. Requires SUPABASE_MGMT_TOKEN +
                            SUPABASE_MGMT_ORG_REF + ENCRYPTION_KEY.
                            Takes 60-90s. Cleanup-strict.

Run:
    cd ai_engine && venv/bin/python scripts/test_step3_content.py
    cd ai_engine && SUPABASE_MGMT_TOKEN=... SUPABASE_MGMT_ORG_REF=... \\
                    ENCRYPTION_KEY=... \\
                    venv/bin/python scripts/test_step3_content.py --live
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Sample schemas ────────────────────────────────────────────────────────

LANDING_SCHEMA = {
    "archetype": "single_page_landing",
    "domain_kind": "saas",
    "brand": {
        "name": "Stonemill Bakery",
        "tagline": "Fresh bread, daily",
        "description": "Family-owned bakery in Brooklyn since 1972.",
        "domain": "stonemill.com",
    },
    "theme": {"primary": "20 80% 50%", "background": "0 0% 100%", "radius": "0.5rem"},
    "design": {"design_system_name": "Harvest Table"},
    "design_system": {"name": "Harvest Table"},
    "navigation": [
        {"group": "main", "items": [
            {"label": "Menu", "path": "/#menu"},
            {"label": "About", "path": "/#about"},
            {"label": "Contact", "path": "/#contact"},
        ]},
    ],
    "pages": [],
    "sections": [
        {"type": "hero", "headline": "Fresh from the oven", "subheadline": "Every morning"},
        {"type": "features", "headline": "Why our bread", "items": ["Stoneground", "Sourdough"]},
        {"type": "cta", "headline": "Visit the shop", "ctaText": "Get directions"},
    ],
    "status_badges": {},
    "api_config": {},
    "mock_db": {},
    "entities": [],
}

CONSUMER_SCHEMA = {
    "archetype": "consumer_website",
    "domain_kind": "professional_services",
    "brand": {"name": "Acme Law", "description": "Boutique corporate law firm.", "tagline": ""},
    "theme": {"primary": "210 60% 40%"},
    "design": {},
    "design_system": {"name": "Editorial Marble"},
    "navigation": [
        {"group": "main", "items": [
            {"label": "Services", "path": "/services"},
            {"label": "About",    "path": "/about"},
            {"label": "Contact",  "path": "/contact"},
        ]},
    ],
    "pages": [
        {"path": "/", "title": "Home", "component": "HomePage", "type": "landing", "sections": [
            {"type": "hero", "headline": "Counsel that moves with you"},
        ]},
        {"path": "/services", "title": "Services", "component": "ServicesPage", "type": "custom", "sections": [
            {"type": "list", "headline": "Practice areas"},
        ]},
        {"path": "/about", "title": "About", "component": "AboutPage", "type": "custom", "sections": []},
    ],
    "sections": [],
    "status_badges": {},
    "api_config": {},
    "mock_db": {},
    "entities": [],
}

ADMIN_SCHEMA = {
    "archetype": "admin_dashboard",
    "domain_kind": "ecommerce",
    "brand": {"name": "Foodly Admin"},
    "theme": {"primary": "0 0% 9%"},
    "design": {},
    "design_system": {"name": "Slate Grid"},
    "navigation": [
        {"group": "Sales", "items": [
            {"label": "Orders", "path": "/orders", "icon": "shopping-cart"},
            {"label": "Customers", "path": "/customers", "icon": "users"},
        ]},
        {"group": "Catalog", "items": [
            {"label": "Products", "path": "/products", "icon": "package"},
        ]},
    ],
    "pages": [
        {"path": "/dashboard", "title": "Dashboard", "component": "DashboardPage", "type": "dashboard", "sections": []},
        {"path": "/orders", "title": "Orders", "component": "OrderListPage", "type": "crud_list", "sections": []},
    ],
    "sections": [],
    "status_badges": {"pending": "bg-amber-100 text-amber-700"},
    "api_config": {"base_url_env": "VITE_API_URL"},
    "entities": [
        {"name": "Order", "slug": "orders", "fields": [
            {"name": "id", "type": "string"},
            {"name": "total", "type": "number"},
        ]},
    ],
    "mock_db": {
        "orders": [
            {"id": "o1", "total": 19.99},
            {"id": "o2", "total": 42.00},
        ],
    },
}


# ── Offline tests ─────────────────────────────────────────────────────────

def test_landing_transforms():
    from app.services.supabase_content import (  # noqa: WPS433
        _build_site_config_row, _build_navigation_rows, _build_page_rows,
        _build_section_rows, _build_entity_rows_and_data,
    )
    site = _build_site_config_row(LANDING_SCHEMA)
    assert site["brand"]["name"] == "Stonemill Bakery"
    assert site["archetype"] == "single_page_landing"
    assert site["domain_kind"] == "saas"

    nav = _build_navigation_rows(LANDING_SCHEMA)
    kinds = {r["kind"] for r in nav}
    assert kinds == {"main", "footer"}, f"landing nav must have main+footer, got {kinds}"
    assert all(r["items"] for r in nav), "no nav row should be empty"

    pages = _build_page_rows(LANDING_SCHEMA)
    assert len(pages) == 1 and pages[0]["path"] == "/", (
        "landing must produce a single synthetic / page"
    )
    page_id_by_path = {pages[0]["path"]: "fake-uuid"}

    sections = _build_section_rows(LANDING_SCHEMA, page_id_by_path)
    assert len(sections) == 3
    assert {s["type"] for s in sections} == {"hero", "features", "cta"}
    assert sections[0]["props"]["headline"] == "Fresh from the oven"
    assert all(s["page_id"] == "fake-uuid" for s in sections)
    assert [s["position"] for s in sections] == [0, 1, 2]

    entities, seed = _build_entity_rows_and_data(LANDING_SCHEMA)
    assert entities == [] and seed == {}, "landing has no entities"
    print("✓ Landing transforms: 1 synthetic page, 3 sections, main+footer nav")


def test_consumer_transforms():
    from app.services.supabase_content import (  # noqa: WPS433
        _build_navigation_rows, _build_page_rows, _build_section_rows,
    )
    nav = _build_navigation_rows(CONSUMER_SCHEMA)
    assert {r["kind"] for r in nav} == {"main", "footer"}

    pages = _build_page_rows(CONSUMER_SCHEMA)
    paths = [p["path"] for p in pages]
    assert paths == ["/", "/services", "/about"], f"page order: {paths}"
    assert pages[1]["component"] == "ServicesPage"

    page_ids = {p["path"]: f"id-{p['path']}" for p in pages}
    sections = _build_section_rows(CONSUMER_SCHEMA, page_ids)
    by_page = {s["page_id"]: [] for s in sections}
    for s in sections:
        by_page[s["page_id"]].append(s["type"])
    assert by_page == {"id-/": ["hero"], "id-/services": ["list"]}, (
        f"sections wrong: {by_page}"
    )
    print(f"✓ Consumer transforms: {len(pages)} pages, {len(sections)} sections (only attached to pages with content)")


def test_admin_transforms():
    from app.services.supabase_content import (  # noqa: WPS433
        _build_navigation_rows, _build_page_rows, _build_entity_rows_and_data,
    )
    nav = _build_navigation_rows(ADMIN_SCHEMA)
    assert all(r["kind"] == "sidebar" for r in nav), (
        f"admin nav must be all sidebar, got {[r['kind'] for r in nav]}"
    )
    group_names = sorted(r["group_name"] for r in nav)
    assert group_names == ["Catalog", "Sales"], group_names

    pages = _build_page_rows(ADMIN_SCHEMA)
    types = {p["type"] for p in pages}
    assert types == {"dashboard", "crud_list"}, types

    entities, seed = _build_entity_rows_and_data(ADMIN_SCHEMA)
    assert len(entities) == 1 and entities[0]["slug"] == "orders"
    assert "orders" in seed and len(seed["orders"]) == 2
    print(f"✓ Admin transforms: 2 sidebar groups, 2 pages, 1 entity, 2 seed rows")


def test_migrations_dir_resolves():
    from app.services.supabase_migrations import _list_migration_files  # noqa: WPS433
    files = _list_migration_files()
    assert any(f.name == "001_content_tables.sql" for f in files), (
        f"001_content_tables.sql not found in {files}"
    )
    sql = files[0].read_text()
    for needed in (
        "gen_site_config", "gen_navigation", "gen_page", "gen_section",
        "gen_entity", "gen_entity_row", "gen_revision",
        "ENABLE ROW LEVEL SECURITY",
    ):
        assert needed in sql, f"migration missing: {needed!r}"
    print("✓ Migration file resolves and contains all 7 tables + RLS")


# ── Live test (real Supabase project lifecycle) ──────────────────────────

async def _live_e2e():
    from app.services.crypto import encrypt, decrypt  # noqa: WPS433
    from app.services.supabase_mgmt import (  # noqa: WPS433
        create_project, wait_until_ready, delete_project,
    )
    from app.services.supabase_migrations import apply_per_project_migrations  # noqa: WPS433
    from app.services.supabase_content import persist_schema_to_supabase  # noqa: WPS433
    from supabase import create_async_client  # noqa: WPS433

    print("  Provisioning new test project…")
    provisioned = await create_project(name="lucid-step3-test", plan="free")
    print(f"  ✓ ref={provisioned.ref}  url={provisioned.url}")

    try:
        print("  Waiting for ACTIVE_HEALTHY…")
        await wait_until_ready(provisioned.ref, timeout_seconds=180.0)
        print("  ✓ Ready")

        # Sanity: encryption round-trips with the project's keys.
        assert decrypt(encrypt(provisioned.service_key).encrypted,
                       encrypt(provisioned.service_key).iv) != provisioned.service_key  # IV changes; this just ensures encrypt always uses fresh IV
        ct = encrypt(provisioned.service_key)
        assert decrypt(ct.encrypted, ct.iv) == provisioned.service_key

        print("  Applying per-project migrations…")
        applied = await apply_per_project_migrations(provisioned.ref)
        print(f"  ✓ Applied: {applied}")

        print("  Persisting CONSUMER_SCHEMA (3 pages, 2 sections, main+footer nav)…")
        summary = await persist_schema_to_supabase(
            project_schema=CONSUMER_SCHEMA,
            supabase_url=provisioned.url,
            service_key=provisioned.service_key,
        )
        print(f"  ✓ Persist summary: {summary}")
        assert summary["site_config"] == 1
        assert summary["page"] == 3
        assert summary["section"] == 2
        # main + footer (one per group; consumer has 1 nav group → 2 total)
        assert summary["navigation"] == 2, f"navigation rows: {summary['navigation']}"

        print("  Verifying via service-role client…")
        client = await create_async_client(provisioned.url, provisioned.service_key)
        site_res = await client.table("gen_site_config").select("brand,archetype,domain_kind").execute()
        assert site_res.data and site_res.data[0]["archetype"] == "consumer_website"
        assert site_res.data[0]["brand"]["name"] == "Acme Law"
        page_res = await client.table("gen_page").select("path,title").execute()
        paths = sorted(r["path"] for r in page_res.data)
        assert paths == ["/", "/about", "/services"], f"paths: {paths}"
        sec_res = await client.table("gen_section").select("type,props").execute()
        assert any(s["type"] == "hero" for s in sec_res.data)
        assert any(s["props"].get("headline") == "Counsel that moves with you" for s in sec_res.data)
        print("  ✓ Verified site_config + 3 pages + sections")

        print("  Verifying anon-key public read of gen_site_config…")
        anon_client = await create_async_client(provisioned.url, provisioned.anon_key)
        anon_res = await anon_client.table("gen_site_config").select("brand").execute()
        assert anon_res.data, "anon client could not read gen_site_config — RLS policy broken"
        print("  ✓ Anon read works — RLS policy correct for content surfaces")
    finally:
        print(f"  Cleaning up: DELETE {provisioned.ref}…")
        try:
            await delete_project(provisioned.ref)
            print("  ✓ Deleted — no billing impact")
        except Exception as exc:
            print(f"  ⚠ Cleanup failed: {exc} — manually delete {provisioned.ref}", file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Run real Supabase end-to-end test (~60-90s; cleanup-strict)")
    args = parser.parse_args()

    saved_env = {
        k: os.environ.get(k)
        for k in ("SUPABASE_MGMT_TOKEN", "SUPABASE_MGMT_ORG_REF", "ENCRYPTION_KEY")
    }

    print("=== Offline tests ===")
    test_landing_transforms()
    test_consumer_transforms()
    test_admin_transforms()
    test_migrations_dir_resolves()
    print("\nAll offline checks passed.")

    if args.live:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
        for mod in (
            "app.config", "app.services.supabase_mgmt",
            "app.services.supabase_migrations", "app.services.supabase_content",
        ):
            sys.modules.pop(mod, None)
        for k in ("SUPABASE_MGMT_TOKEN", "SUPABASE_MGMT_ORG_REF", "ENCRYPTION_KEY"):
            if not os.environ.get(k):
                print(f"\n--live requires {k} in env", file=sys.stderr)
                sys.exit(1)
        print("\n=== Live e2e (creates and deletes a real Supabase project) ===")
        asyncio.run(_live_e2e())
        print("\nLive test passed.")


if __name__ == "__main__":
    main()
