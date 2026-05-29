"""Mock-based unit tests for `app.services.admin_pipeline`.

No live Supabase, no LLM calls. The pipeline's external services
(classify_purpose, analyze_intent, plan_data_model, the three tenant
helpers, and the supabase admin client) are all patched on the module
namespace so we can assert call counts + arguments + return-value
plumbing in isolation.

Suites:
  • TestRouting           — should_route_to_admin_pipeline + flag gating
  • TestStandaloneFlow    — provisioning + seed for non-linked admins
  • TestLinkedFlow        — parent resolution + skipped provisioning
  • TestStageFailures     — hard-failure paths return False
  • TestProgressUpdates   — websocket progress messages are sent
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services import admin_pipeline  # noqa: E402
from app.services.admin_pipeline import (  # noqa: E402
    ADMIN_V2_ARCHETYPES,
    run_admin_pipeline,
    should_route_to_admin_pipeline,
)


# ── Fixture data ─────────────────────────────────────────────────────

UUID_VALID    = "11111111-2222-3333-4444-555555555555"
UUID_PARENT   = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SCHEMA_NEW    = "tenant_112222333344"
SCHEMA_PARENT = "tenant_aabbccddeeee"


def _make_admin_data_model() -> DataModel:
    """Two-table model that resembles a typical CRM admin."""
    return DataModel(
        version="1.0",
        tables=[
            TableDefinition(
                name="leads",
                singular_label="Lead",
                plural_label="Leads",
                description="Sales leads.",
                fields=[
                    FieldDefinition(name="name",  type="text", required=True),
                    FieldDefinition(name="email", type="text", required=True),
                ],
            ),
            TableDefinition(
                name="contacts",
                singular_label="Contact",
                plural_label="Contacts",
                description="Customer contact records.",
                fields=[
                    FieldDefinition(name="name",  type="text", required=True),
                    FieldDefinition(name="phone", type="text", required=False),
                ],
            ),
        ],
        singletons={},
    )


def _fake_purpose() -> dict:
    return {
        "primary_purpose":  "operational",
        "confidence":       90,
        "industry":         "internal_tool",
        "target_audience":  "internal_team",
        "named_roles":      ["sales", "ops"],
    }


def _fake_intent() -> dict:
    return {
        "business_category":     "internal_tool",
        "geographic_specifics":  "United States",
        "tone":                  "professional",
        "brand_personality":     ["calm", "efficient"],
        "brand":                 {"name": "OpsCo"},
        "target_audience":       {"primary": "internal team"},
    }


def _make_chat_session_admin_client(
    *,
    parent_project_id: str | None = None,
    update_raises: Exception | None = None,
) -> MagicMock:
    """A managed_admin_client double that supports:
      .table('chat_sessions').update({...}).eq('id', X).execute()  (product_type write)
      .table('chat_sessions').select(...).eq('id', X).limit(1).execute()
          → returns {'parent_project_id': ...}

    The product_type update path is only exercised when the project_id
    is a real UUID, so the caller doesn't always touch this client.
    """
    client = MagicMock()

    chain = MagicMock()
    chain.select.return_value = chain
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain

    if update_raises is not None:
        chain.execute = AsyncMock(side_effect=update_raises)
    else:
        chain.execute = AsyncMock(return_value=MagicMock(
            data=[{"parent_project_id": parent_project_id}],
        ))

    client.table.return_value = chain
    client.rpc = MagicMock()
    return client


def _patch_admin_client(client) -> "patch":
    @asynccontextmanager
    async def _fake_managed():
        yield client
    # The module imports managed_admin_client lazily inside the
    # pipeline function — patch the source so both call sites pick it
    # up (the product_type write AND the linked-parent lookup).
    return patch(
        "app.supabase_client.managed_admin_client",
        new=_fake_managed,
    )


# ── Auto-patch the noisy LLM/external services for every test ────────
# Tests opt INTO the routing-only path by passing a non-admin archetype
# or flag off; in those cases these patches are still active but
# unused, which is fine.

@pytest.fixture
def patched_externals(monkeypatch):
    """Patch every external dependency at the `admin_pipeline` module
    boundary. Returns a dict of mocks so individual tests can override.

    These are the leaves the pipeline calls into:
      • classify_purpose          → purpose_classifier
      • analyze_intent            → landing_intent
      • plan_admin_data_model     → admin_data_model_planner (Step 3.4)
      • provision_tenant_for_project + seed_tenant_for_project +
        resolve_tenant_for_project → pipeline_tenant

    Each patch hits the IMPORT site inside the pipeline function (lazy
    imports), so we patch the source module — that way every reference
    is consistent regardless of where it's imported from.

    Note: the `plan_data_model` key is the *test-side* alias for the
    admin planner mock. It's a stable name across Step 3.3 → 3.4 so
    individual tests don't need to know which planner the pipeline
    delegates to.
    """
    # The admin planner short-circuits to `parent_data_model` when one
    # is passed (linked-admin path). Mirror that behaviour in the mock
    # so individual tests don't have to know which path the pipeline
    # takes — they just set `resolve_tenant_for_project.return_value`
    # for linked and leave it alone for standalone.
    _default_dm = _make_admin_data_model()

    async def _planner_short_circuit(
        *,
        parent_data_model=None,
        **kwargs,
    ):
        if parent_data_model is not None:
            return parent_data_model
        return planner_mock.return_value

    planner_mock = AsyncMock(side_effect=_planner_short_circuit)
    planner_mock.return_value = _default_dm  # for standalone tests

    # Default brand-extractor mock — standalone admins go through this
    # at Stage 3. Returns a complete 6-field dict so the downstream
    # plan + foundation builder never see Nones. Tests that want to
    # observe extractor calls (or override the return) set this on the
    # returned mocks dict.
    _default_brand_signals = {
        "brand_name":         "OpsCo",
        "primary_color":      "#0f172a",
        "typography_voice":   "professional",
        "cultural_intensity": "calm",
        "layout_density":     "comfortable",
        "accent_motif":       "geometric",
    }

    mocks: dict = {
        "classify_purpose":              AsyncMock(return_value=_fake_purpose()),
        "analyze_intent":                AsyncMock(return_value=_fake_intent()),
        "plan_data_model":               planner_mock,
        "provision_tenant_for_project":  AsyncMock(return_value=SCHEMA_NEW),
        "seed_tenant_for_project":       AsyncMock(return_value={
            "success": True, "tables_inserted": 2, "rows_inserted": 8,
        }),
        "resolve_tenant_for_project":    AsyncMock(),
        "extract_admin_brand_signals":   AsyncMock(return_value=_default_brand_signals),
    }
    monkeypatch.setattr(
        "app.services.purpose_classifier.classify_purpose",
        mocks["classify_purpose"],
    )
    monkeypatch.setattr(
        "app.services.landing_intent.analyze_intent",
        mocks["analyze_intent"],
    )
    monkeypatch.setattr(
        "app.services.admin_data_model_planner.plan_admin_data_model",
        mocks["plan_data_model"],
    )
    monkeypatch.setattr(
        "app.services.pipeline_tenant.provision_tenant_for_project",
        mocks["provision_tenant_for_project"],
    )
    monkeypatch.setattr(
        "app.services.pipeline_tenant.seed_tenant_for_project",
        mocks["seed_tenant_for_project"],
    )
    monkeypatch.setattr(
        "app.services.pipeline_tenant.resolve_tenant_for_project",
        mocks["resolve_tenant_for_project"],
    )
    monkeypatch.setattr(
        "app.services.admin_brand_extractor.extract_admin_brand_signals",
        mocks["extract_admin_brand_signals"],
    )
    return mocks


# ─────────────────────────────────────────────────────────────────────
#  TestRouting
# ─────────────────────────────────────────────────────────────────────

class TestRouting:

    @pytest.mark.asyncio
    async def test_admin_pipeline_skipped_when_flag_off(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "0")
        result = await run_admin_pipeline(
            description="lead manager",
            classification={"layout_archetype": "admin_dashboard"},
            workspace_path=str(tmp_path),
            validated={},
            websocket=None,
            chat_session_id=UUID_VALID,
        )
        assert result is False

    def test_admin_pipeline_invoked_when_flag_on_and_archetype_match(self, monkeypatch):
        """should_route_to_admin_pipeline gates on flag AND archetype."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "true")
        for archetype in ADMIN_V2_ARCHETYPES:
            assert should_route_to_admin_pipeline(archetype) is True, (
                f"flag-on + archetype={archetype!r} should route"
            )

    def test_admin_pipeline_skipped_for_non_admin_archetypes(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "true")
        for archetype in ("consumer_website", "portfolio", "blog", "marketplace"):
            assert should_route_to_admin_pipeline(archetype) is False, (
                f"flag-on + archetype={archetype!r} must NOT route to admin pipeline"
            )

    def test_admin_pipeline_skipped_for_ecommerce_archetype(self, monkeypatch):
        """Ecommerce intentionally stays on legacy in Step 3.3."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "true")
        assert should_route_to_admin_pipeline("ecommerce") is False


# ─────────────────────────────────────────────────────────────────────
#  TestStandaloneFlow — non-linked admin: own tenant + seed
# ─────────────────────────────────────────────────────────────────────

class TestStandaloneFlow:

    @pytest.mark.asyncio
    async def test_standalone_admin_provisions_own_tenant(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="internal CRM for sales leads + contacts",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        # Provision was called exactly once with our project_id.
        patched_externals["provision_tenant_for_project"].assert_awaited_once()
        kwargs = patched_externals["provision_tenant_for_project"].await_args.kwargs
        assert kwargs["project_id"] == UUID_VALID
        # resolve_tenant should NOT be called for a standalone project.
        patched_externals["resolve_tenant_for_project"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_standalone_admin_persists_data_model(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """plan_data_model is called and its result flows to provision."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="internal tool",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        patched_externals["plan_data_model"].assert_awaited_once()
        # The data_model passed to provision should be the one the
        # planner returned.
        provision_kwargs = (
            patched_externals["provision_tenant_for_project"].await_args.kwargs
        )
        assert provision_kwargs["data_model"] is patched_externals[
            "plan_data_model"
        ].return_value

    @pytest.mark.asyncio
    async def test_standalone_admin_calls_seed(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            await run_admin_pipeline(
                description="internal CRM",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        patched_externals["seed_tenant_for_project"].assert_awaited_once()
        kwargs = patched_externals["seed_tenant_for_project"].await_args.kwargs
        assert kwargs["tenant_schema"] == SCHEMA_NEW
        assert kwargs["project_id"] == UUID_VALID

    @pytest.mark.asyncio
    async def test_standalone_admin_extracts_own_visual_dna(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """Standalone admins call the brand extractor at Stage 3 and
        skip the parent visual_dna path (since there's no parent)."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="internal CRM",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        # Brand extractor was called exactly once with the upstream
        # intent + purpose_data — never with a parent_visual_dna kwarg
        # (that's the linked path).
        patched_externals["extract_admin_brand_signals"].assert_awaited_once()
        kwargs = patched_externals["extract_admin_brand_signals"].await_args.kwargs
        assert "intent" in kwargs
        assert "purpose_data" in kwargs
        # Standalone never resolves a parent — no resolve call.
        patched_externals["resolve_tenant_for_project"].assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────
#  TestLinkedFlow — admin with parent_project_id
# ─────────────────────────────────────────────────────────────────────

class TestLinkedFlow:

    @pytest.mark.asyncio
    async def test_linked_admin_resolves_parent_tenant(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        parent_dm = _make_admin_data_model()
        patched_externals["resolve_tenant_for_project"].return_value = (
            SCHEMA_PARENT, parent_dm, None,
        )
        client = _make_chat_session_admin_client(parent_project_id=UUID_PARENT)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="ops admin attached to existing website",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        patched_externals["resolve_tenant_for_project"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_linked_admin_skips_provisioning(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        patched_externals["resolve_tenant_for_project"].return_value = (
            SCHEMA_PARENT, _make_admin_data_model(), None,
        )
        client = _make_chat_session_admin_client(parent_project_id=UUID_PARENT)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="linked admin",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        # Provisioning is reused from the parent — no new schema work.
        patched_externals["provision_tenant_for_project"].assert_not_awaited()
        # The planner IS called (uniform Stage 4.5 entry) but with
        # parent_data_model set, which short-circuits to the parent's
        # tables without a Gemini call.
        planner_calls = patched_externals["plan_data_model"].await_args_list
        assert len(planner_calls) == 1
        assert planner_calls[0].kwargs.get("parent_data_model") is not None, (
            "linked admin must pass parent_data_model to short-circuit "
            "the planner, not trigger a real Gemini call"
        )

    @pytest.mark.asyncio
    async def test_linked_admin_skips_seeding(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        patched_externals["resolve_tenant_for_project"].return_value = (
            SCHEMA_PARENT, _make_admin_data_model(), None,
        )
        client = _make_chat_session_admin_client(parent_project_id=UUID_PARENT)
        with _patch_admin_client(client):
            await run_admin_pipeline(
                description="linked admin",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        # Parent already seeded — don't duplicate.
        patched_externals["seed_tenant_for_project"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_linked_admin_uses_parent_data_model(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """The plan + downstream stages should see the parent's tables.

        Step 3.4 changed the contract: `plan_admin_data_model` is
        always called (uniform Stage 4.5) but short-circuits to
        `parent_data_model` when given one. Assert that path was
        taken, AND that the parent's actual tables flow downstream
        to provision/seed (so neither receives a fresh planner result).
        """
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        # Use a distinctive parent DataModel so we can prove its
        # identity downstream.
        parent_dm = DataModel(
            version="1.0",
            tables=[
                TableDefinition(
                    name="parent_table_distinct",
                    singular_label="X",
                    plural_label="Xs",
                    description="Parent-only marker.",
                    fields=[FieldDefinition(name="name", type="text", required=True)],
                    public_read=False,
                ),
            ],
            singletons={},
        )
        patched_externals["resolve_tenant_for_project"].return_value = (
            SCHEMA_PARENT, parent_dm, None,
        )
        client = _make_chat_session_admin_client(parent_project_id=UUID_PARENT)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="linked admin",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        # Planner was called once with parent_data_model = parent_dm,
        # short-circuiting before any Gemini work.
        planner_calls = patched_externals["plan_data_model"].await_args_list
        assert len(planner_calls) == 1
        assert planner_calls[0].kwargs.get("parent_data_model") is parent_dm

    @pytest.mark.asyncio
    async def test_linked_admin_inherits_parent_visual_dna(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """When the parent has a visual_dna persisted, the linked admin
        uses it verbatim and skips the standalone brand extractor."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        parent_dm = _make_admin_data_model()
        parent_visual_dna = {
            "brand_name":         "Studio Vibrant",
            "primary_color":      "#ff3366",
            "typography_voice":   "editorial",
            "cultural_intensity": "energetic",
            "layout_density":     "spacious",
            "accent_motif":       "organic",
            # extras the website pipeline persists — admin doesn't read
            # them but must not blow up when they're present.
            "section_anatomies":  {"hero": "…"},
            "decorative_motifs":  ["wave", "asterisk"],
        }
        patched_externals["resolve_tenant_for_project"].return_value = (
            SCHEMA_PARENT, parent_dm, parent_visual_dna,
        )
        client = _make_chat_session_admin_client(parent_project_id=UUID_PARENT)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="linked admin",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is True
        # Linked admin must NOT call the brand extractor — it inherits.
        patched_externals["extract_admin_brand_signals"].assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────
#  TestStageFailures — hard failures must return False
# ─────────────────────────────────────────────────────────────────────

class TestStageFailures:

    @pytest.mark.asyncio
    async def test_failed_planning_returns_false(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """plan_data_model returns an empty DataModel → False."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        patched_externals["plan_data_model"].return_value = DataModel(
            version="1.0", tables=[], singletons={},
        )
        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="too vague",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is False
        # And we never reached provisioning.
        patched_externals["provision_tenant_for_project"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_provisioning_returns_false_for_standalone(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """provision_tenant_for_project returns None → hard failure for admins."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        patched_externals["provision_tenant_for_project"].return_value = None
        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="ops admin",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is False
        # And no seeding past a failed provision.
        patched_externals["seed_tenant_for_project"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_resolve_returns_false_for_linked(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        """parent_project_id set but resolve_tenant_for_project returns
        None → linked admin can't proceed, return False."""
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        patched_externals["resolve_tenant_for_project"].return_value = None
        client = _make_chat_session_admin_client(parent_project_id=UUID_PARENT)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="linked admin pointing at unprovisioned parent",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=None,
                chat_session_id=UUID_VALID,
            )
        assert ok is False


# ─────────────────────────────────────────────────────────────────────
#  TestProgressUpdates — websocket plumbing
# ─────────────────────────────────────────────────────────────────────

class TestProgressUpdates:

    @pytest.mark.asyncio
    async def test_websocket_messages_sent_for_each_stage(
        self, monkeypatch, tmp_path, patched_externals,
    ):
        monkeypatch.setenv("ADMIN_PIPELINE_V2_ENABLED", "1")
        sent_messages: list[dict] = []
        ws = MagicMock()
        async def _send_json(payload):
            sent_messages.append(payload)
        ws.send_json = _send_json

        client = _make_chat_session_admin_client(parent_project_id=None)
        with _patch_admin_client(client):
            ok = await run_admin_pipeline(
                description="ops admin",
                classification={"layout_archetype": "admin_dashboard"},
                workspace_path=str(tmp_path),
                validated={},
                websocket=ws,
                chat_session_id=UUID_VALID,
            )
        assert ok is True

        # At minimum: a progress message lands for the "understand
        # the request" step, the "design the data" step, and a final
        # "ready" message. We don't pin exact strings — pipelines
        # tweak copy frequently and the test should track *intent*.
        progress_msgs = [m["message"] for m in sent_messages if m.get("type") == "progress"]
        assert any(
            "understand" in m.lower() or "purpose" in m.lower() or "studying" in m.lower()
            for m in progress_msgs
        ), f"no purpose/intent message in {progress_msgs!r}"
        assert any(
            "data" in m.lower() or "manage" in m.lower() or "entities" in m.lower()
            for m in progress_msgs
        ), f"no data-model message in {progress_msgs!r}"
        # Many progress messages — the chat should show smooth motion.
        # 5 is a conservative floor for the post-refactor pipeline
        # (it currently emits ~10 with 4 entities).
        assert len(progress_msgs) >= 5, (
            f"expected many progress messages, got {len(progress_msgs)}: {progress_msgs!r}"
        )
        # Final "ready" message regardless of exact copy.
        assert any(
            "ready" in m.lower() for m in progress_msgs
        ), f"no completion message in {progress_msgs!r}"
