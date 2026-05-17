"""Tests for the admin-aware Stage 4.5 planner.

Two layers (mirroring `test_data_model_planner.py`):
  • Unit tests — mock `structured_distill`; cover the linked-mode
    short-circuit + fallback to empty DataModel on persistent failure.
  • Live tests — gated on `@pytest.mark.live`. Run real Gemini against
    four domain prompts (CRM, restaurant ops, property mgmt, helpdesk)
    and assert the planner produces admin-shape tables.

Live tests cost ~$0.04/run × 4 prompts ≈ $0.16, but the
`class`-scoped fixtures mean each prompt runs ONCE per pytest invocation
and is reused across the assertions, so the practical cost is ~$0.16
per run (one live call per fixture).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(str(Path(__file__).resolve().parents[2] / ".env"))


from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.admin_data_model_planner import (  # noqa: E402
    plan_admin_data_model,
)


# ── Live-mode gate ────────────────────────────────────────────────────

def _have_gemini() -> bool:
    return bool(
        os.environ.get("GOOGLE_API_KEY", "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )


skip_no_gemini = pytest.mark.skipif(
    not _have_gemini(),
    reason="No Gemini credentials (set GOOGLE_API_KEY or ADC)",
)


# ── Helpers ───────────────────────────────────────────────────────────

def _table_names(model: DataModel) -> list[str]:
    return [t.name for t in model.tables]


def _has_any_table_matching(model: DataModel, *substrings: str) -> bool:
    """True if any table name contains any of the given substrings.

    Gemini synonyms vary — "contacts" might come back as "people",
    "leads" as "prospects". Asserts on intent, not exact wording.
    """
    names = " ".join(_table_names(model)).lower()
    return any(s.lower() in names for s in substrings)


def _intent_for(prompt: str, category: str = "internal_tool") -> dict:
    return {
        "original_prompt":      prompt,
        "business_category":    category,
        "business_subcategory": "operational",
        "tone":                 "professional",
    }


def _purpose_for(industry: str) -> dict:
    return {
        "primary_purpose":  "operational",
        "target_audience":  "internal_team",
        "industry":         industry,
    }


# ─────────────────────────────────────────────────────────────────────
#  Unit tests — mocked Gemini
# ─────────────────────────────────────────────────────────────────────

class TestLinkedMode:
    """parent_data_model provided → return it without calling Gemini."""

    @pytest.mark.asyncio
    async def test_returns_parent_directly(self):
        parent = DataModel(
            version="1.0",
            tables=[
                TableDefinition(
                    name="leads",
                    singular_label="Lead",
                    plural_label="Leads",
                    description="Sales leads.",
                    fields=[
                        FieldDefinition(name="name", type="text", required=True),
                    ],
                    public_read=False,
                ),
            ],
            singletons={},
        )
        with patch(
            "app.services.admin_data_model_planner.structured_distill",
            new=AsyncMock(),
        ) as mock_call:
            result = await plan_admin_data_model(
                intent={"original_prompt": "any"},
                purpose_data={},
                gemini_key="",
                parent_data_model=parent,
                project_id="linked-admin",
            )
            mock_call.assert_not_awaited()
        assert result is parent
        assert _table_names(result) == ["leads"]


class TestFallback:
    """Persistent Gemini failure → empty DataModel."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_persistent_failure(self):
        with patch(
            "app.services.admin_data_model_planner.structured_distill",
            new=AsyncMock(side_effect=RuntimeError("gemini down")),
        ) as mock_call:
            result = await plan_admin_data_model(
                intent=_intent_for("manage leads"),
                purpose_data=_purpose_for("crm"),
                gemini_key="",
                project_id="fallback-test",
            )
        # Two attempts then give up.
        assert mock_call.await_count == 2
        assert result.tables == []
        assert result.singletons == {}


class TestPublicReadCoercion:
    """Even if Gemini returns public_read=true, the planner forces False."""

    @pytest.mark.asyncio
    async def test_coerces_public_read_to_false(self):
        bad_response = """\
{
  "tables": [
    {
      "name": "contacts",
      "singular_label": "Contact",
      "plural_label": "Contacts",
      "description": "Customer contacts.",
      "fields": [
        {"name": "name", "type": "text", "required": true},
        {"name": "email", "type": "email", "required": true}
      ],
      "indexes": ["email"],
      "public_read": true
    }
  ],
  "singletons": {}
}
"""
        with patch(
            "app.services.admin_data_model_planner.structured_distill",
            new=AsyncMock(return_value=bad_response),
        ):
            result = await plan_admin_data_model(
                intent=_intent_for("manage contacts"),
                purpose_data=_purpose_for("crm"),
                gemini_key="",
                project_id="coerce-test",
            )
        assert len(result.tables) == 1
        assert result.tables[0].public_read is False, (
            "admin planner must coerce public_read=True to False"
        )


# ─────────────────────────────────────────────────────────────────────
#  Live tests — real Gemini, one call per fixture
# ─────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="class")
async def crm_model_live():
    return await plan_admin_data_model(
        intent=_intent_for(
            "internal tool to manage customer leads and contacts. "
            "Sales reps add new leads, mark them qualified, "
            "convert them to contacts with company + phone + email.",
            category="crm",
        ),
        purpose_data=_purpose_for("customer_relationship_management"),
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-crm",
    )


@pytest_asyncio.fixture(scope="class")
async def restaurant_ops_model_live():
    return await plan_admin_data_model(
        intent=_intent_for(
            "admin for managing my restaurant — track reservations, "
            "customers, daily orders. Each reservation has a customer, "
            "date/time, party size. Each order has line items and a total.",
            category="restaurant_ops",
        ),
        purpose_data=_purpose_for("restaurant_operations"),
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-restaurant-ops",
    )


@pytest_asyncio.fixture(scope="class")
async def property_mgmt_model_live():
    return await plan_admin_data_model(
        intent=_intent_for(
            "property management dashboard for tracking tenants, "
            "leases, and maintenance requests across multiple buildings. "
            "Each tenant has a lease tied to a unit; maintenance "
            "requests come in tied to a unit.",
            category="property_management",
        ),
        purpose_data=_purpose_for("property_management"),
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-property-mgmt",
    )


@pytest_asyncio.fixture(scope="class")
async def helpdesk_model_live():
    return await plan_admin_data_model(
        intent=_intent_for(
            "support ticket system for our SaaS company. Customers "
            "submit tickets; support agents assign, update status, "
            "and close them. Each ticket has a priority and category.",
            category="helpdesk",
        ),
        purpose_data=_purpose_for("customer_support"),
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-helpdesk",
    )


@skip_no_gemini
@pytest.mark.live
@pytest.mark.asyncio
class TestAdminPlannerLive:
    """Per-domain assertions that the planner produces admin-shape tables.

    Each test class reuses one cached planner call via the class-scoped
    fixture, so the per-class wall-clock cost is one Gemini Pro 3.1
    call (~$0.04).
    """

    # ── CRM ──────────────────────────────────────────────────────────

    async def test_crm_prompt_returns_crm_entities(self, crm_model_live):
        names = _table_names(crm_model_live)
        # CRM prompt MUST produce a leads or contacts table (or both)
        # and MUST NOT have website-shape junk.
        assert _has_any_table_matching(crm_model_live, "lead", "contact", "customer"), (
            f"expected CRM entities (leads/contacts/customers), got {names}"
        )
        # The Step 3.3 failure shape — make sure we never repeat it.
        for forbidden in ("updates", "faqs", "resources", "hero"):
            assert not _has_any_table_matching(crm_model_live, forbidden), (
                f"admin planner regressed to website shape: {forbidden!r} in {names}"
            )

    # ── Restaurant ops ───────────────────────────────────────────────

    async def test_restaurant_ops_prompt(self, restaurant_ops_model_live):
        names = _table_names(restaurant_ops_model_live)
        # Operational tables — reservations are the strongest signal.
        # Accept any of reservations/customers/orders as evidence the
        # planner read the prompt as operations, not marketing.
        assert _has_any_table_matching(
            restaurant_ops_model_live, "reservation", "customer", "order",
        ), f"expected restaurant-ops entities, got {names}"

    # ── Property management ──────────────────────────────────────────

    async def test_property_management_prompt(self, property_mgmt_model_live):
        names = _table_names(property_mgmt_model_live)
        # The prompt mentions tenants + leases + maintenance — at
        # least one of those clusters should land.
        assert _has_any_table_matching(
            property_mgmt_model_live,
            "tenant", "lease", "maintenance", "propert",
        ), f"expected property-mgmt entities, got {names}"

    # ── Helpdesk ─────────────────────────────────────────────────────

    async def test_helpdesk_prompt(self, helpdesk_model_live):
        names = _table_names(helpdesk_model_live)
        # tickets are the strongest signal; customers/agents are
        # reasonable secondary expectations.
        assert _has_any_table_matching(
            helpdesk_model_live, "ticket", "customer", "agent",
        ), f"expected helpdesk entities, got {names}"
        # Should NOT have a marketing FAQ singleton tucked in.
        for forbidden_singleton in ("hero", "footer", "testimonials"):
            assert forbidden_singleton not in helpdesk_model_live.singletons, (
                f"unexpected marketing singleton {forbidden_singleton!r} "
                f"in admin model: {list(helpdesk_model_live.singletons)}"
            )

    # ── Cross-cutting invariants ─────────────────────────────────────

    async def test_returns_empty_singletons_for_admin(
        self,
        crm_model_live,
        restaurant_ops_model_live,
        property_mgmt_model_live,
        helpdesk_model_live,
    ):
        """Admin models shouldn't carry marketing singletons. We
        accept admin_branding-style config keys but flag anything
        clearly marketing."""
        marketing_singletons = {
            "hero", "about", "footer", "testimonials", "features",
            "pricing", "faq", "faqs",
        }
        for label, model in [
            ("crm",          crm_model_live),
            ("restaurant",   restaurant_ops_model_live),
            ("property_mgmt",property_mgmt_model_live),
            ("helpdesk",     helpdesk_model_live),
        ]:
            offending = set(model.singletons.keys()) & marketing_singletons
            assert not offending, (
                f"{label} admin has marketing singletons: {offending} "
                f"(full singletons: {list(model.singletons.keys())})"
            )

    async def test_all_tables_have_public_read_false(
        self,
        crm_model_live,
        restaurant_ops_model_live,
        property_mgmt_model_live,
        helpdesk_model_live,
    ):
        for label, model in [
            ("crm",          crm_model_live),
            ("restaurant",   restaurant_ops_model_live),
            ("property_mgmt",property_mgmt_model_live),
            ("helpdesk",     helpdesk_model_live),
        ]:
            for t in model.tables:
                assert t.public_read is False, (
                    f"{label} table {t.name!r} has public_read=True — "
                    "admin data must be private"
                )
