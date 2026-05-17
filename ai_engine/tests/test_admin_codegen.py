"""Tests for the Step 3.6 Part A admin CRUD codegen plumbing.

Three suites:
  • TestPromptBuilders — shape + content of the 3 prompt templates.
  • TestMockMode      — `generate_entity_crud(mock=True)` end-to-end.
  • TestValidation    — static validator catches the contract
                        violations the spec calls out.

All tests are mock-only — no Claude API, no network. The real-Claude
path is exercised in Step 3.6 Part B.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.admin_codegen import generate_entity_crud  # noqa: E402
from app.services.admin_codegen_validator import (  # noqa: E402
    validate_generated_crud_file,
    validate_imports,
)
from app.services.admin_plan import build_admin_plan  # noqa: E402
from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.prompts import (  # noqa: E402
    build_create_view_prompt,
    build_edit_view_prompt,
    build_list_view_prompt,
)


# ── Fixture helpers ──────────────────────────────────────────────────

def _entity(name: str = "leads") -> TableDefinition:
    return TableDefinition(
        name=name,
        singular_label="Lead",
        plural_label="Leads",
        description="Sales leads under management.",
        fields=[
            FieldDefinition(name="first_name", type="text",  required=True),
            FieldDefinition(name="email",      type="email", required=True),
            FieldDefinition(name="phone",      type="phone", required=False),
            FieldDefinition(name="status",     type="text",  required=True,
                            enum_values=["new", "qualified", "lost"]),
            FieldDefinition(name="created_by", type="text",  required=False,
                            max_length=200),
            FieldDefinition(name="is_active",  type="boolean", required=False),
        ],
        public_read=False,
    )


def _plan(entity: TableDefinition) -> dict:
    dm = DataModel(version="1.0", tables=[entity], singletons={})
    return build_admin_plan(dm, {"brand_name": "OpsCo", "primary_color": "#0f172a"})


# ─────────────────────────────────────────────────────────────────────
#  TestPromptBuilders — shape + content of the 3 prompts
# ─────────────────────────────────────────────────────────────────────

class TestPromptBuilders:

    def test_list_view_prompt_includes_entity_fields(self):
        ent = _entity()
        out = build_list_view_prompt(ent, _plan(ent))
        assert "system" in out and "user" in out
        body = out["user"]
        # Every field name should appear in the field block
        for field in ent.fields:
            assert field.name in body, f"missing {field.name} in list prompt"
        # The entity name shows up too
        assert ent.name in body

    def test_create_view_prompt_includes_field_types(self):
        ent = _entity()
        out = build_create_view_prompt(ent, _plan(ent))
        body = out["user"] + out["system"]
        # System prompt enumerates type → input mapping
        for t in ("email", "boolean", "datetime-local", "checkbox"):
            assert t in body, f"create prompt missing type '{t}'"
        # User prompt mentions react-hook-form
        assert "useForm" in body
        assert "createRow" in body

    def test_edit_view_prompt_includes_id_fetching(self):
        ent = _entity()
        out = build_edit_view_prompt(ent, _plan(ent))
        body = out["user"]
        # Reads params.id, finds row, pre-fills
        assert "params.id" in body
        assert "find(" in body or "find((r)" in body
        assert "reset(" in body or "defaultValues" in body
        # Submit path
        assert "updateRow" in body
        # Delete affordance
        assert "deleteRow" in body

    def test_all_prompts_forbid_typescript(self):
        ent = _entity()
        for builder in (build_list_view_prompt, build_create_view_prompt, build_edit_view_prompt):
            sys_p = builder(ent, _plan(ent))["system"]
            # The system prompts must explicitly forbid TS syntax.
            assert "TypeScript" in sys_p
            assert "no TypeScript" in sys_p.lower() or "No TypeScript syntax" in sys_p

    def test_all_prompts_require_authguard(self):
        ent = _entity()
        for builder in (build_list_view_prompt, build_create_view_prompt, build_edit_view_prompt):
            out = builder(ent, _plan(ent))
            assert "AuthGuard" in out["system"]
            assert "AuthGuard" in out["user"]

    def test_all_prompts_use_db_admin_helpers(self):
        ent = _entity()
        helpers = {
            build_list_view_prompt:   "listCollection",
            build_create_view_prompt: "createRow",
            build_edit_view_prompt:   "updateRow",
        }
        for builder, helper in helpers.items():
            out = builder(ent, _plan(ent))
            text = out["system"] + out["user"]
            assert helper in text, f"{builder.__name__} missing {helper}"
            assert "@/lib/db_admin" in text


# ─────────────────────────────────────────────────────────────────────
#  TestMockMode — generate_entity_crud(mock=True) end-to-end
# ─────────────────────────────────────────────────────────────────────

class TestMockMode:

    def _run(self, tmp_path, entity=None):
        ent = entity or _entity()
        return asyncio.run(generate_entity_crud(
            entity=ent,
            data_model=DataModel(version="1.0", tables=[ent], singletons={}),
            admin_plan=_plan(ent),
            workspace_path=str(tmp_path),
            anthropic_key="",
            mock=True,
        ))

    def test_mock_writes_three_pages_per_entity(self, tmp_path):
        result = self._run(tmp_path)
        # 3 pages: list / create / edit
        assert len(result["files_written"]) == 3
        for tail in ("page.jsx", "new/page.jsx", "[id]/page.jsx"):
            assert (tmp_path / f"src/app/leads/{tail}").is_file()

    def test_mock_files_valid_jsx_contain_use_client(self, tmp_path):
        self._run(tmp_path)
        for tail in ("page.jsx", "new/page.jsx", "[id]/page.jsx"):
            content = (tmp_path / f"src/app/leads/{tail}").read_text()
            # The directive must be the first non-comment line so
            # Next's RSC bailout works.
            first_line = next(
                (l for l in content.splitlines() if l.strip() and not l.strip().startswith("/*")),
                "",
            )
            assert first_line.strip() == '"use client";', (
                f"{tail} doesn't lead with use client: {first_line!r}"
            )

    def test_mock_includes_authguard(self, tmp_path):
        self._run(tmp_path)
        list_jsx = (tmp_path / "src/app/leads/page.jsx").read_text()
        assert "AuthGuard" in list_jsx
        assert "@/components/AuthGuard" in list_jsx

    def test_mock_passes_validator(self, tmp_path):
        result = self._run(tmp_path)
        # Mock files should produce zero validator issues.
        assert result["validation_errors"] == [], (
            f"mock pages failed validation: {result['validation_errors']!r}"
        )

    def test_mock_zero_actual_cost(self, tmp_path):
        result = self._run(tmp_path)
        assert result["claude_actual_cost"] == 0.0
        # But the estimate should be > 0 — it's per-page budget × 3.
        assert result["claude_cost_estimate"] > 0

    def test_snake_case_entity_writes_kebab_path(self, tmp_path):
        ent = _entity("purchase_orders")
        self._run(tmp_path, entity=ent)
        assert (tmp_path / "src/app/purchase-orders/page.jsx").is_file()
        assert (tmp_path / "src/app/purchase-orders/new/page.jsx").is_file()
        assert (tmp_path / "src/app/purchase-orders/[id]/page.jsx").is_file()


# ─────────────────────────────────────────────────────────────────────
#  TestValidation — static checks fire / pass as expected
# ─────────────────────────────────────────────────────────────────────

_VALID_LIST_JSX = '''\
"use client";
import { useEffect, useState } from "react";
import { listCollection, deleteRow } from "@/lib/db_admin.js";
import { AuthGuard } from "@/components/AuthGuard.jsx";

export default function LeadsListPage() {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    (async () => {
      const data = await listCollection("leads");
      setRows(data);
    })();
  }, []);
  return (
    <AuthGuard>
      <div>{rows.map((row) => <div key={row.id}>{row.name}</div>)}</div>
    </AuthGuard>
  );
}
'''


class TestValidation:

    def test_valid_crud_file_passes(self):
        issues = validate_generated_crud_file(
            _VALID_LIST_JSX, expected_entity="leads", page_type="list",
        )
        assert issues == []

    def test_empty_file_fails(self):
        issues = validate_generated_crud_file(
            "", expected_entity="leads",
        )
        assert "empty file" in issues

    def test_missing_use_client_fails(self):
        broken = _VALID_LIST_JSX.replace('"use client";\n', "")
        issues = validate_generated_crud_file(broken, expected_entity="leads")
        assert any("use client" in i for i in issues), issues

    def test_missing_authguard_fails(self):
        broken = _VALID_LIST_JSX.replace("AuthGuard", "Wrapper").replace(
            '@/components/Wrapper.jsx', '@/components/AuthGuard.jsx',
        )
        # First replace knocked out AuthGuard references; the second
        # restored the import path so we only test the "not used" case.
        broken = broken.replace(
            '@/components/AuthGuard.jsx',
            '@/components/Wrapper.jsx',
        )
        issues = validate_generated_crud_file(broken, expected_entity="leads")
        assert any("AuthGuard" in i for i in issues)

    def test_missing_db_admin_import_fails(self):
        broken = _VALID_LIST_JSX.replace("@/lib/db_admin.js", "@/lib/local-mock.js")
        issues = validate_generated_crud_file(broken, expected_entity="leads")
        assert any("db_admin" in i for i in issues)

    def test_typescript_syntax_fails(self):
        ts_bad = '''\
"use client";
import { listCollection } from "@/lib/db_admin.js";
import { AuthGuard } from "@/components/AuthGuard.jsx";

interface LeadsProps { rows: Lead[] }

export default function LeadsListPage(props: LeadsProps) {
  return <AuthGuard><div>{props.rows.length}</div></AuthGuard>;
}
'''
        issues = validate_generated_crud_file(
            ts_bad, expected_entity="leads",
        )
        # Two TS smells expected: interface declaration + typed params.
        assert any("TypeScript" in i for i in issues), issues

    def test_fetch_call_fails(self):
        bad_fetch = '''\
"use client";
import { listCollection } from "@/lib/db_admin.js";
import { AuthGuard } from "@/components/AuthGuard.jsx";

export default function LeadsListPage() {
  async function run() { const r = await fetch("/api/leads"); }
  return <AuthGuard><div>leads</div></AuthGuard>;
}
'''
        issues = validate_generated_crud_file(
            bad_fetch, expected_entity="leads",
        )
        assert any("fetch" in i.lower() for i in issues)

    def test_direct_supabase_import_fails(self):
        bad = '''\
"use client";
import { createClient } from "@supabase/supabase-js";
import { AuthGuard } from "@/components/AuthGuard.jsx";
import { listCollection } from "@/lib/db_admin.js";
export default function LeadsListPage() {
  return <AuthGuard><div>leads</div></AuthGuard>;
}
'''
        issues = validate_imports(bad)
        assert any("@supabase" in i for i in issues), issues

    def test_unbalanced_jsx_fails(self):
        broken = '''\
"use client";
import { listCollection } from "@/lib/db_admin.js";
import { AuthGuard } from "@/components/AuthGuard.jsx";

export default function LeadsListPage() {
  return (
    <AuthGuard>
      <div>leads
    </AuthGuard>
  );
}
'''
        # The bracket balance check focuses on (), {}, []. JSX tags
        # aren't parsed here — instead validate that an unclosed `(`
        # gets caught.
        broken_brackets = '''\
"use client";
import { listCollection } from "@/lib/db_admin.js";
import { AuthGuard } from "@/components/AuthGuard.jsx";
export default function LeadsListPage() {
  const arr = [1, 2, 3;
  return <AuthGuard><div>{arr}</div></AuthGuard>;
}
'''
        issues = validate_generated_crud_file(
            broken_brackets, expected_entity="leads",
        )
        assert any("bracket" in i.lower() or "unclosed" in i.lower() for i in issues), issues

    def test_top_level_await_fails(self):
        bad = '''\
"use client";
import { listCollection } from "@/lib/db_admin.js";
import { AuthGuard } from "@/components/AuthGuard.jsx";

const rows = await listCollection("leads");

export default function LeadsListPage() {
  return <AuthGuard><div>{rows.length}</div></AuthGuard>;
}
'''
        issues = validate_generated_crud_file(
            bad, expected_entity="leads",
        )
        assert any("await" in i.lower() for i in issues), issues

    def test_entity_name_must_appear(self):
        # Valid otherwise but doesn't mention 'leads'
        bad = _VALID_LIST_JSX.replace("leads", "contacts")
        issues = validate_generated_crud_file(
            bad, expected_entity="leads",
        )
        assert any("leads" in i for i in issues)
