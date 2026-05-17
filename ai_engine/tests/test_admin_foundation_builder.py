"""Unit tests for `app.services.admin_foundation_builder`.

Pure I/O — no LLM, no Supabase, no network. Each test invokes
`build_admin_foundation` against a tmp directory and asserts the
files emitted have the expected content.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.admin_foundation_builder import (  # noqa: E402
    _hex_to_hsl_string,
    build_admin_foundation,
)
from app.services.admin_plan import build_admin_plan  # noqa: E402
from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)


# ── Fixture data ─────────────────────────────────────────────────────

UUID_VALID = "11111111-2222-3333-4444-555555555555"
SCHEMA     = "tenant_112222333344"
SUPA_URL   = "https://example.supabase.co"
SUPA_ANON  = "anon-key-xxx"


def _table(name: str, singular: str, plural: str) -> TableDefinition:
    return TableDefinition(
        name=name,
        singular_label=singular,
        plural_label=plural,
        description=f"{plural} for the admin.",
        fields=[
            FieldDefinition(name="name", type="text", required=True),
            FieldDefinition(name="email", type="email", required=False),
        ],
        public_read=False,
    )


def _make_three_entity_model() -> DataModel:
    return DataModel(
        version="1.0",
        tables=[
            _table("contacts", "Contact", "Contacts"),
            _table("leads",    "Lead",    "Leads"),
            _table("orders",   "Order",   "Orders"),
        ],
        singletons={},
    )


def _visual_dna(brand: str = "OpsCo", color: str = "#0f172a") -> dict:
    return {
        "brand_name":    brand,
        "primary_color": color,
        "logo_url":      None,
    }


@pytest.fixture
def baseline_result(tmp_path):
    """Run the foundation builder once with three entities so multiple
    tests can read the same workspace without rebuilding."""
    dm = _make_three_entity_model()
    plan = build_admin_plan(dm, _visual_dna())
    result = build_admin_foundation(
        workspace_path=str(tmp_path),
        data_model=dm,
        admin_plan=plan,
        tenant_schema=SCHEMA,
        project_id=UUID_VALID,
        supabase_url=SUPA_URL,
        supabase_anon_key=SUPA_ANON,
        seed_counts={"contacts": 5, "leads": 0, "orders": 3},
    )
    return {"workspace": tmp_path, "result": result, "data_model": dm, "plan": plan}


def _read(workspace: Path, rel: str) -> str:
    return (workspace / rel).read_text()


# ─────────────────────────────────────────────────────────────────────
#  TestFoundationFiles
# ─────────────────────────────────────────────────────────────────────

class TestFoundationFiles:

    def test_writes_package_json_with_correct_deps(self, baseline_result):
        pkg = json.loads(_read(baseline_result["workspace"], "package.json"))
        assert pkg["name"] == "opsco-admin"
        deps = pkg["dependencies"]
        # Spot-check every required dep mentioned in the spec.
        for required in (
            "next", "react", "react-dom",
            "@supabase/ssr", "@supabase/supabase-js",
            "react-hook-form", "lucide-react",
            "clsx", "tailwind-merge",
        ):
            assert required in deps, f"missing dep: {required}"
        # And the build scripts
        assert pkg["scripts"]["dev"]   == "next dev"
        assert pkg["scripts"]["build"] == "next build"

    def test_writes_next_config(self, baseline_result):
        cfg = _read(baseline_result["workspace"], "next.config.js")
        assert "reactStrictMode" in cfg
        assert "module.exports" in cfg

    def test_writes_tailwind_config_with_primary_color(self, tmp_path):
        # Use a non-default color so we can prove primary_color flows
        # into the generated globals.css via the HSL conversion.
        dm = _make_three_entity_model()
        plan = build_admin_plan(dm, _visual_dna(color="#ff0066"))
        build_admin_foundation(
            workspace_path=str(tmp_path),
            data_model=dm, admin_plan=plan,
            tenant_schema=SCHEMA, project_id=UUID_VALID,
            supabase_url=SUPA_URL, supabase_anon_key=SUPA_ANON,
        )
        # tailwind.config.js itself is fixed (it references the CSS
        # vars). The primary color shows up in globals.css.
        tw = _read(tmp_path, "tailwind.config.js")
        assert "hsl(var(--primary))" in tw
        # globals.css carries the HSL conversion of the brand color.
        css = _read(tmp_path, "src/app/globals.css")
        expected_hsl = _hex_to_hsl_string("#ff0066")
        assert f"--primary:              {expected_hsl}" in css, css

    def test_writes_env_local_with_credentials(self, baseline_result):
        env = _read(baseline_result["workspace"], ".env.local")
        assert f"NEXT_PUBLIC_SUPABASE_URL={SUPA_URL}" in env
        assert f"NEXT_PUBLIC_SUPABASE_ANON_KEY={SUPA_ANON}" in env
        assert f"NEXT_PUBLIC_PROJECT_ID={UUID_VALID}" in env
        assert f"NEXT_PUBLIC_TENANT_SCHEMA={SCHEMA}" in env
        assert "NEXT_PUBLIC_BRAND_NAME=OpsCo" in env
        assert "NEXT_PUBLIC_PRIMARY_COLOR=#0f172a" in env

    def test_writes_supabase_js(self, baseline_result):
        src = _read(baseline_result["workspace"], "src/lib/supabase.js")
        assert "createBrowserClient" in src
        assert "@supabase/ssr" in src
        assert "getSupabaseBrowserClient" in src
        # Throws on missing env vars
        assert "throw new Error" in src

    def test_writes_db_admin_js_with_all_four_helpers(self, baseline_result):
        src = _read(baseline_result["workspace"], "src/lib/db_admin.js")
        # Authenticated read uses 027
        assert "get_tenant_collection_authenticated" in src
        # Writes use 026
        assert '"set_tenant_row"' in src
        assert '"update_tenant_row"' in src
        assert '"delete_tenant_row"' in src
        # Exported helpers
        for fn in ("listCollection", "createRow", "updateRow", "deleteRow"):
            assert f"export async function {fn}" in src
        # Documentation comment lists every entity so the file is
        # self-describing when opened directly.
        for name in ("contacts", "leads", "orders"):
            assert name in src

    def test_writes_auth_js(self, baseline_result):
        src = _read(baseline_result["workspace"], "src/lib/auth.js")
        assert "useAuth" in src
        assert "signOut" in src
        assert "onAuthStateChange" in src
        # Redirects on SIGNED_OUT
        assert '"/login"' in src

    def test_writes_sidebar_with_navigation(self, baseline_result):
        src = _read(baseline_result["workspace"], "src/components/Sidebar.jsx")
        # Each entity becomes a nav item
        for slug in ("/contacts", "/leads", "/orders"):
            assert slug in src
        # Dashboard nav item also present
        assert '"/"' in src or '"/", "icon": "home"' in src
        # Imports come from lucide-react
        assert "from \"lucide-react\"" in src

    def test_writes_login_page(self, baseline_result):
        src = _read(baseline_result["workspace"], "src/app/login/page.jsx")
        assert "signInWithPassword" in src
        assert "type=\"email\"" in src
        assert "type=\"password\"" in src

    def test_writes_dashboard_page_with_entity_counts(self, baseline_result):
        src = _read(baseline_result["workspace"], "src/app/page.jsx")
        assert "listCollection" in src
        # Dashboard tiles per entity
        for slug in ("/contacts", "/leads", "/orders"):
            assert slug in src


# ─────────────────────────────────────────────────────────────────────
#  TestEntityPages
# ─────────────────────────────────────────────────────────────────────

class TestEntityPages:

    def test_creates_three_pages_per_entity(self, baseline_result):
        ws = baseline_result["workspace"]
        # 3 entities × 3 pages = 9 entity pages
        for entity in ("contacts", "leads", "orders"):
            assert (ws / f"src/app/{entity}/page.jsx").is_file()
            assert (ws / f"src/app/{entity}/new/page.jsx").is_file()
            assert (ws / f"src/app/{entity}/[id]/page.jsx").is_file()

    def test_stub_pages_explain_step_3_6(self, baseline_result):
        ws = baseline_result["workspace"]
        list_page = _read(ws, "src/app/contacts/page.jsx")
        new_page  = _read(ws, "src/app/contacts/new/page.jsx")
        edit_page = _read(ws, "src/app/contacts/[id]/page.jsx")
        # Stub pages reference Step 3.6 so a developer reading them
        # in isolation knows where the real implementation lives.
        for src in (list_page, new_page, edit_page):
            assert "Step 3.6" in src

    def test_paths_match_admin_plan_routes(self, baseline_result):
        plan = baseline_result["plan"]
        ws = baseline_result["workspace"]
        for page in plan["pages"]:
            if page["page_type"] in ("auth", "dashboard", "layout"):
                continue  # Shared shells handled separately
            entity = page["entity"]
            route = page["route"]  # e.g. /admin/contacts
            # admin_plan emits routes under /admin/<entity>. The
            # foundation builder mounts the project at the root, so
            # /admin/contacts maps to src/app/contacts/page.jsx.
            # Strip the /admin prefix to compare:
            assert route.startswith(f"/admin/{entity.replace('_', '-')}")
            # And the file exists at the corresponding path:
            slug = entity.replace("_", "-")
            tail = route[len(f"/admin/{slug}"):]
            page_path = f"src/app/{slug}{tail}/page.jsx"
            assert (ws / page_path).is_file(), (
                f"{page['route']} → expected {page_path} but missing"
            )

    def test_snake_case_entity_becomes_kebab_route_dir(self, tmp_path):
        dm = DataModel(
            version="1.0",
            tables=[_table("purchase_orders", "Purchase Order", "Purchase Orders")],
            singletons={},
        )
        plan = build_admin_plan(dm, _visual_dna())
        build_admin_foundation(
            workspace_path=str(tmp_path),
            data_model=dm, admin_plan=plan,
            tenant_schema=SCHEMA, project_id=UUID_VALID,
            supabase_url=SUPA_URL, supabase_anon_key=SUPA_ANON,
        )
        for tail in ("page.jsx", "new/page.jsx", "[id]/page.jsx"):
            assert (tmp_path / f"src/app/purchase-orders/{tail}").is_file()


# ─────────────────────────────────────────────────────────────────────
#  TestEmptyVsSeededHandling
# ─────────────────────────────────────────────────────────────────────

class TestEmptyVsSeededHandling:

    def test_seeded_entities_marked_in_result(self, baseline_result):
        # Baseline seed_counts: contacts=5, leads=0, orders=3
        seeded = baseline_result["result"]["tables_with_seed_data"]
        empty  = baseline_result["result"]["tables_empty"]
        assert "contacts" in seeded
        assert "orders"   in seeded
        assert "leads"    in empty

    def test_empty_state_component_generated(self, baseline_result):
        ws = baseline_result["workspace"]
        src = _read(ws, "src/components/EmptyState.jsx")
        assert "export function EmptyState" in src
        assert "No {label}" in src

    def test_seed_count_appears_in_stub_list_page(self, baseline_result):
        ws = baseline_result["workspace"]
        contacts_list = _read(ws, "src/app/contacts/page.jsx")
        assert "Seed data: 5 Contacts" in contacts_list
        leads_list = _read(ws, "src/app/leads/page.jsx")
        assert "Seed data: 0 Leads" in leads_list

    def test_no_seed_counts_passed_treated_as_all_empty(self, tmp_path):
        dm = _make_three_entity_model()
        plan = build_admin_plan(dm, _visual_dna())
        result = build_admin_foundation(
            workspace_path=str(tmp_path),
            data_model=dm, admin_plan=plan,
            tenant_schema=SCHEMA, project_id=UUID_VALID,
            supabase_url=SUPA_URL, supabase_anon_key=SUPA_ANON,
            # seed_counts intentionally None
        )
        # Every table goes into `tables_empty` when no counts are provided
        assert set(result["tables_empty"]) == {"contacts", "leads", "orders"}
        assert result["tables_with_seed_data"] == []


# ─────────────────────────────────────────────────────────────────────
#  TestColorConversion — HSL helper
# ─────────────────────────────────────────────────────────────────────

class TestColorConversion:

    @pytest.mark.parametrize("hex_in, expected", [
        ("#000000", "0 0% 0%"),
        ("#ffffff", "0 0% 100%"),
        ("#0f172a", "222 47% 11%"),     # slate-900
        ("#FF0066", "336 100% 50%"),    # vibrant pink
        ("#fff",    "0 0% 100%"),       # 3-char form
    ])
    def test_known_hex_to_hsl(self, hex_in, expected):
        assert _hex_to_hsl_string(hex_in) == expected

    @pytest.mark.parametrize("bad", ["", "#xxx", "notacolor", "#12345"])
    def test_bad_input_falls_back_to_slate(self, bad):
        assert _hex_to_hsl_string(bad) == "222 47% 11%"
