"""Dump generator output for the three example DataModels.

Run during development to refresh `ai_engine/docs/SAMPLE_GENERATED_SQL.md`
so the reference doc never drifts from the actual generator output.

Usage:
    docker exec lucid-ai-ai_engine-1 python scripts/show_sample_sql.py \
        > docs/SAMPLE_GENERATED_SQL.md
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.data_model import (
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.tenant_sql_generator import generate_tenant_sql


# Fixed UUIDs + schema name so the sample SQL is byte-stable across
# runs (modulo the embedded timestamp). Easier to review diffs.
SAMPLE_SCHEMA = "tenant_sample_001"
SAMPLE_PROJECT_ID = "00000000-1111-2222-3333-444444444444"


def _restaurant() -> DataModel:
    return DataModel(
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item", plural_label="Menu Items",
                description="Dishes the restaurant sells.",
                fields=[
                    FieldDefinition(name="name", type="text", required=True, max_length=120),
                    FieldDefinition(name="description", type="text"),
                    FieldDefinition(name="price_cents", type="integer", required=True),
                    FieldDefinition(name="photo_url", type="image_url"),
                    FieldDefinition(
                        name="category", type="text",
                        enum_values=["starter", "main", "dessert", "drink"],
                    ),
                    FieldDefinition(name="is_available", type="boolean", default="true"),
                ],
                indexes=["category"],
                public_read=True,
            ),
            TableDefinition(
                name="reservations",
                singular_label="Reservation", plural_label="Reservations",
                description="Bookings made by guests.",
                fields=[
                    FieldDefinition(name="guest_name", type="text", required=True),
                    FieldDefinition(name="guest_email", type="email", required=True),
                    FieldDefinition(name="party_size", type="integer", required=True),
                    FieldDefinition(name="reserved_for", type="datetime", required=True),
                ],
                public_read=False,
            ),
        ],
    )


def _ecommerce() -> DataModel:
    return DataModel(
        tables=[
            TableDefinition(
                name="products",
                singular_label="Product", plural_label="Products",
                description="Items for sale.",
                fields=[
                    FieldDefinition(name="name", type="text", required=True),
                    FieldDefinition(name="slug", type="text", required=True),
                    FieldDefinition(name="price_cents", type="integer", required=True),
                    FieldDefinition(name="stock_count", type="integer", default="0"),
                    FieldDefinition(name="is_active", type="boolean", default="true"),
                ],
                indexes=["slug"],
                public_read=True,
            ),
            TableDefinition(
                name="orders",
                singular_label="Order", plural_label="Orders",
                description="Customer purchases.",
                fields=[
                    FieldDefinition(name="customer_email", type="email", required=True),
                    FieldDefinition(name="total_cents", type="integer", required=True),
                    FieldDefinition(
                        name="status", type="text", required=True, default="'pending'",
                        enum_values=["pending", "paid", "shipped"],
                    ),
                    FieldDefinition(name="line_items", type="json", required=True),
                ],
                public_read=False,
            ),
        ],
    )


def _blog() -> DataModel:
    return DataModel(
        tables=[
            TableDefinition(
                name="posts",
                singular_label="Post", plural_label="Posts",
                description="Articles.",
                fields=[
                    FieldDefinition(name="title", type="text", required=True, max_length=200),
                    FieldDefinition(name="slug", type="text", required=True),
                    FieldDefinition(name="body_markdown", type="text", required=True),
                    FieldDefinition(name="hero_image_url", type="image_url"),
                    FieldDefinition(name="published_at", type="datetime"),
                    FieldDefinition(
                        name="status", type="text", required=True, default="'draft'",
                        enum_values=["draft", "published", "archived"],
                    ),
                ],
                indexes=["slug", "status"],
                public_read=True,
            ),
        ],
    )


def main() -> int:
    print("# Sample generated SQL")
    print()
    print("Auto-generated from `scripts/show_sample_sql.py` against the three")
    print("example DataModels in [DATA_MODEL_FORMAT.md](DATA_MODEL_FORMAT.md).")
    print("Schema name and project UUID are fixed placeholders so re-running")
    print("the script produces byte-stable output (modulo the embedded")
    print("generation timestamp). Refresh after any change to")
    print("`app/services/tenant_sql_generator.py`:")
    print()
    print("```bash")
    print("docker exec lucid-ai-ai_engine-1 python scripts/show_sample_sql.py \\")
    print("    > docs/SAMPLE_GENERATED_SQL.md")
    print("```")
    print()

    for label, builder in [
        ("Restaurant", _restaurant),
        ("E-commerce", _ecommerce),
        ("Blog", _blog),
    ]:
        print(f"## {label}")
        print()
        print("```sql")
        print(generate_tenant_sql(builder(), SAMPLE_SCHEMA, SAMPLE_PROJECT_ID).rstrip())
        print("```")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
