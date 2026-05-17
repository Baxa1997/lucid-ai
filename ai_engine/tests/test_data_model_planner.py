"""Tests for the Stage 4.5 data-model planner.

Two layers:
  • Unit tests — mock `structured_distill`; cover parse/retry/fallback
    logic without spending Gemini budget.
  • Live tests — gated on @pytest.mark.live. Run real Gemini against
    each of the 5 fixture plans. ~$0.01 total per run.

Fixtures live under `tests/fixtures/website_plans/*.json` so the same
inputs feed both the unit-test mocks and the live sample-doc script.
"""
from __future__ import annotations

import json
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


from app.services.data_model import DataModel  # noqa: E402
from app.services.data_model_planner import plan_data_model  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures" / "website_plans"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ── Live-mode gate ────────────────────────────────────────────────────

def _have_gemini() -> bool:
    """True when a real Gemini key (or Vertex ADC) is available."""
    # The Gemini helper auths via Vertex ADC when GOOGLE_APPLICATION_CREDENTIALS
    # or a service account is present; we treat the GOOGLE_API_KEY env as the
    # cheap signal. Either one missing = skip.
    return bool(
        os.environ.get("GOOGLE_API_KEY", "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )


skip_no_gemini = pytest.mark.skipif(
    not _have_gemini(),
    reason="No Gemini credentials (set GOOGLE_API_KEY or ADC)",
)


# ── Unit tests (mocked Gemini) ───────────────────────────────────────

class TestParseAndValidation:
    """Cover parse/validate/retry/fallback without spending Gemini budget."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_validated_model(self):
        good = json.dumps({
            "tables": [{
                "name": "menu_items",
                "singular_label": "Menu Item",
                "plural_label": "Menu Items",
                "description": "Dishes the restaurant sells.",
                "fields": [
                    {"name": "name", "type": "text", "required": True},
                    {"name": "price_cents", "type": "integer", "required": True},
                ],
                "indexes": [],
                "public_read": True,
            }],
            "singletons": {"hero": {"title": "string"}},
        })
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=AsyncMock(return_value=good),
        ):
            model = await plan_data_model(
                website_plan=_load("restaurant_plan.json"),
                intent={"business_category": "restaurant"},
                purpose_data={"primary_purpose": "brand_awareness"},
                gemini_key="",
            )
        assert isinstance(model, DataModel)
        assert [t.name for t in model.tables] == ["menu_items"]
        assert "hero" in model.singletons

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fence(self):
        """Gemini sometimes ignores the 'no fences' instruction."""
        fenced = "```json\n" + json.dumps({"tables": [], "singletons": {}}) + "\n```"
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=AsyncMock(return_value=fenced),
        ):
            model = await plan_data_model(
                website_plan=_load("saas_landing_plan.json"),
                intent={}, purpose_data={}, gemini_key="",
            )
        assert model.tables == []

    @pytest.mark.asyncio
    async def test_retry_on_invalid_field_name(self):
        """First response has a non-snake_case field name → retry → second
        response is clean → model returned."""
        bad = json.dumps({
            "tables": [{
                "name": "menu_items",
                "singular_label": "Menu Item",
                "plural_label": "Menu Items",
                "description": "x",
                "fields": [{"name": "PriceCents", "type": "integer"}],
                "indexes": [], "public_read": True,
            }],
            "singletons": {},
        })
        good = json.dumps({
            "tables": [{
                "name": "menu_items",
                "singular_label": "Menu Item",
                "plural_label": "Menu Items",
                "description": "x",
                "fields": [{"name": "price_cents", "type": "integer"}],
                "indexes": [], "public_read": True,
            }],
            "singletons": {},
        })
        mock = AsyncMock(side_effect=[bad, good])
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=mock,
        ):
            model = await plan_data_model(
                website_plan=_load("restaurant_plan.json"),
                intent={}, purpose_data={}, gemini_key="",
            )
        assert mock.await_count == 2
        assert model.tables[0].fields[0].name == "price_cents"

    @pytest.mark.asyncio
    async def test_fallback_to_empty_on_persistent_parse_failure(self):
        """Two attempts both return garbage → return empty DataModel."""
        mock = AsyncMock(side_effect=["not json", "still not json"])
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=mock,
        ):
            model = await plan_data_model(
                website_plan=_load("blog_plan.json"),
                intent={}, purpose_data={}, gemini_key="",
            )
        assert mock.await_count == 2
        assert isinstance(model, DataModel)
        assert model.tables == []
        assert model.singletons == {}

    @pytest.mark.asyncio
    async def test_fallback_to_empty_on_persistent_validation_failure(self):
        """Two attempts both fail snake_case → empty model."""
        bad = json.dumps({
            "tables": [{
                "name": "MenuItems",  # PascalCase → fails validate_data_model
                "singular_label": "x", "plural_label": "x", "description": "x",
                "fields": [{"name": "name", "type": "text"}],
                "indexes": [], "public_read": True,
            }],
            "singletons": {},
        })
        mock = AsyncMock(side_effect=[bad, bad])
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=mock,
        ):
            model = await plan_data_model(
                website_plan=_load("restaurant_plan.json"),
                intent={}, purpose_data={}, gemini_key="",
            )
        assert mock.await_count == 2
        assert model.tables == []

    @pytest.mark.asyncio
    async def test_reserved_field_names_stripped_silently(self):
        """Gemini sometimes emits id/created_at/updated_at; planner drops them
        before validation rather than burning a retry."""
        polluted = json.dumps({
            "tables": [{
                "name": "menu_items",
                "singular_label": "Menu Item", "plural_label": "Menu Items",
                "description": "x",
                "fields": [
                    {"name": "id", "type": "text"},          # reserved — dropped
                    {"name": "name", "type": "text"},
                    {"name": "created_at", "type": "datetime"},  # reserved — dropped
                ],
                "indexes": [], "public_read": True,
            }],
            "singletons": {},
        })
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=AsyncMock(return_value=polluted),
        ):
            model = await plan_data_model(
                website_plan=_load("restaurant_plan.json"),
                intent={}, purpose_data={}, gemini_key="",
            )
        assert [f.name for f in model.tables[0].fields] == ["name"]

    @pytest.mark.asyncio
    async def test_fallback_when_gemini_raises(self):
        """A provider error (timeout, 5xx, etc.) is treated as a failed
        attempt — same retry-then-fallback behavior."""
        mock = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "app.services.data_model_planner.structured_distill",
            new=mock,
        ):
            model = await plan_data_model(
                website_plan=_load("portfolio_plan.json"),
                intent={}, purpose_data={}, gemini_key="",
            )
        assert mock.await_count == 2
        assert model.tables == []


# ── Live tests (real Gemini) ─────────────────────────────────────────

@pytest_asyncio.fixture(scope="class")
async def restaurant_model_live():
    return await plan_data_model(
        website_plan=_load("restaurant_plan.json"),
        intent={
            "business_category": "restaurant",
            "geographic_specifics": "Brooklyn, NY",
            "tone": "warm",
        },
        purpose_data={"primary_purpose": "brand_awareness", "target_audience": "b2c_consumers"},
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-restaurant",
    )


@pytest_asyncio.fixture(scope="class")
async def ecommerce_model_live():
    return await plan_data_model(
        website_plan=_load("ecommerce_plan.json"),
        intent={"business_category": "ecommerce", "tone": "curated"},
        purpose_data={"primary_purpose": "conversion", "target_audience": "b2c_consumers"},
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-ecommerce",
    )


@pytest_asyncio.fixture(scope="class")
async def blog_model_live():
    return await plan_data_model(
        website_plan=_load("blog_plan.json"),
        intent={"business_category": "content", "tone": "personal"},
        purpose_data={"primary_purpose": "brand_awareness", "target_audience": "developers"},
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-blog",
    )


@pytest_asyncio.fixture(scope="class")
async def portfolio_model_live():
    return await plan_data_model(
        website_plan=_load("portfolio_plan.json"),
        intent={"business_category": "creative", "tone": "minimalist"},
        purpose_data={"primary_purpose": "brand_awareness", "target_audience": "b2b_clients"},
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-portfolio",
    )


@pytest_asyncio.fixture(scope="class")
async def saas_model_live():
    return await plan_data_model(
        website_plan=_load("saas_landing_plan.json"),
        intent={"business_category": "saas", "tone": "professional"},
        purpose_data={"primary_purpose": "conversion", "target_audience": "b2b_decision_makers"},
        gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        project_id="live-test-saas",
    )


def _table_names(model: DataModel) -> list[str]:
    return [t.name for t in model.tables]


def _has_any_table_matching(model: DataModel, *substrings: str) -> bool:
    """True if any table name contains any of the given substrings.

    Used because Gemini synonyms vary: "menu_items" vs "dishes",
    "team_members" vs "staff" vs "people". Tests assert intent, not
    exact wording.
    """
    names = " ".join(_table_names(model))
    return any(s in names for s in substrings)


@skip_no_gemini
@pytest.mark.live
@pytest.mark.asyncio
class TestRestaurantDataModel:
    async def test_restaurant_includes_menu_items(self, restaurant_model_live):
        assert _has_any_table_matching(restaurant_model_live, "menu", "dish"), \
            f"expected a menu/dishes table, got {_table_names(restaurant_model_live)}"

    async def test_restaurant_includes_gallery_or_testimonials(self, restaurant_model_live):
        assert _has_any_table_matching(
            restaurant_model_live, "gallery", "testimonial", "review", "image", "photo",
        ), f"expected gallery or testimonials, got {_table_names(restaurant_model_live)}"

    async def test_restaurant_singletons_include_hero(self, restaurant_model_live):
        assert "hero" in restaurant_model_live.singletons, (
            f"hero must be a singleton, got keys: "
            f"{list(restaurant_model_live.singletons.keys())}"
        )

    async def test_restaurant_no_table_for_about(self, restaurant_model_live):
        # The story/about section is one-of-a-kind copy — never a table.
        bad_names = {n for n in _table_names(restaurant_model_live)
                     if "about" in n or "story" in n}
        assert not bad_names, f"about/story should be singleton, got tables: {bad_names}"


@skip_no_gemini
@pytest.mark.live
@pytest.mark.asyncio
class TestEcommerceDataModel:
    async def test_ecommerce_includes_products(self, ecommerce_model_live):
        assert _has_any_table_matching(ecommerce_model_live, "product", "item"), \
            f"expected products table, got {_table_names(ecommerce_model_live)}"

    async def test_products_have_price_and_image(self, ecommerce_model_live):
        products = next(
            (t for t in ecommerce_model_live.tables
             if "product" in t.name or "item" in t.name),
            None,
        )
        assert products is not None
        field_names = {f.name for f in products.fields}
        has_price = any("price" in fn for fn in field_names)
        has_image = any("image" in fn or "photo" in fn or "cover" in fn for fn in field_names)
        assert has_price, f"products missing a price field: {field_names}"
        assert has_image, f"products missing an image field: {field_names}"

    async def test_products_is_public_read(self, ecommerce_model_live):
        products = next(
            (t for t in ecommerce_model_live.tables
             if "product" in t.name or "item" in t.name),
            None,
        )
        assert products is not None
        assert products.public_read is True


@skip_no_gemini
@pytest.mark.live
@pytest.mark.asyncio
class TestBlogDataModel:
    async def test_blog_includes_posts(self, blog_model_live):
        assert _has_any_table_matching(blog_model_live, "post", "article", "entry"), \
            f"expected posts table, got {_table_names(blog_model_live)}"

    async def test_posts_have_slug_field(self, blog_model_live):
        posts = next(
            (t for t in blog_model_live.tables
             if "post" in t.name or "article" in t.name),
            None,
        )
        assert posts is not None
        assert any(f.name == "slug" for f in posts.fields), \
            f"posts missing slug: {[f.name for f in posts.fields]}"

    async def test_posts_has_published_date(self, blog_model_live):
        posts = next(
            (t for t in blog_model_live.tables
             if "post" in t.name or "article" in t.name),
            None,
        )
        assert posts is not None
        field_names = {f.name for f in posts.fields}
        # Accept any sensible synonym
        assert any(
            "published" in fn or fn == "date" or fn == "publish_date"
            for fn in field_names
        ), f"posts missing a publish-date field: {field_names}"


@skip_no_gemini
@pytest.mark.live
@pytest.mark.asyncio
class TestPortfolioDataModel:
    async def test_portfolio_has_projects_or_works_table(self, portfolio_model_live):
        assert _has_any_table_matching(
            portfolio_model_live, "project", "work", "case", "portfolio",
        ), f"expected projects/works table, got {_table_names(portfolio_model_live)}"


@skip_no_gemini
@pytest.mark.live
@pytest.mark.asyncio
class TestSaasLandingDataModel:
    async def test_minimal_or_empty_tables(self, saas_model_live):
        """A single-page SaaS landing is allowed to have 0 tables — all
        sections (features, pricing, FAQ) are fixed marketing copy.
        A non-zero count is also acceptable (e.g. if Gemini decides FAQ
        is a table), as long as no obviously-singleton fields slipped in."""
        # No "hero", "pricing", or "comparison" tables — those are fixed copy
        bad = {n for n in _table_names(saas_model_live)
               if n in {"hero", "heros", "pricing", "comparison", "comparisons"}}
        assert not bad, f"singleton sections leaked into tables: {bad}"
        # Hero should be in singletons
        assert "hero" in saas_model_live.singletons, (
            f"hero must be a singleton, got keys: "
            f"{list(saas_model_live.singletons.keys())}"
        )
