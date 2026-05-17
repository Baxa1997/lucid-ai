"""Data-model schema shared by website and admin generators.

This module is the *contract* between the two pipelines. Both the
website pipeline and the (forthcoming) admin pipeline describe their
project's runtime data using the same shape — `DataModel` — so the
schemas they emit are interchangeable and the future
"generate-admin-for-this-website" feature can read one product's
DataModel and feed it to the other.

What lives here
---------------
* `FieldDefinition`  — one column of one table.
* `TableDefinition`  — one entity (e.g. "menu_items").
* `DataModel`        — the full description of one project's data.
* `validate_data_model(model)` — semantic checks beyond Pydantic types
  (snake_case naming, no duplicates, enum coherence).
* `normalize_field_name`, `normalize_table_name` — slug helpers that
  convert LLM-emitted display text to snake_case.

What does NOT live here
-----------------------
* SQL generation — see the (future) tenant_provisioner / DDL builder.
* Pipeline integration — this module is pipeline-agnostic by design.
* Relationships / foreign keys — deferred to v2. `TableDefinition` has
  a `relationships` slot that accepts arbitrary dicts so v1 emitters
  can hint at them without committing the schema.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Types we know how to map to SQL ──────────────────────────────────
# Closed set. New types require a coordinated change to both the
# validator (here) and the DDL generator (later). Closed-set wins over
# free-form so a typo in an LLM emission becomes a Pydantic 422 instead
# of a silent CREATE TABLE with a garbage column type.
FieldType = Literal[
    "text",        # short string; default for free-form input
    "number",      # decimal; maps to NUMERIC(12,2). Use for money, ratings, etc.
    "integer",     # whole number; maps to BIGINT. Use for counts, sort_order, etc.
    "boolean",     # true / false
    "date",        # calendar date, no time component
    "datetime",    # timestamp with time zone
    "json",        # opaque JSONB blob (use sparingly — kills query-ability)
    "image_url",   # url to an image; behaves as text + UI affordance
    "email",       # text + email-validator on write
    "phone",       # text + loose phone validator on write
    "url",         # text + url validator on write
]


_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ── Models ────────────────────────────────────────────────────────────

class FieldDefinition(BaseModel):
    """One column of one entity table.

    `type` is mandatory and Pydantic enforces the closed set above.
    `enum_values` may be set when the field is a `text` whose value is
    chosen from a fixed list (e.g. order.status ∈ {pending, paid,
    shipped}). Outside of `text`, `enum_values` must be None.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="snake_case column name")
    type: FieldType
    required: bool = False
    default: Optional[str] = Field(
        default=None,
        description="SQL DEFAULT expression as a string. None ⇒ no default.",
    )

    @field_validator("default", mode="before")
    @classmethod
    def _coerce_default_to_sql_str(cls, v: Any) -> Optional[str]:
        # Gemini Pro emits native JSON bool/int/float for SQL defaults
        # ("default": false, "default": 0). Coerce to the SQL literal
        # form so downstream code keeps a single string contract.
        # bool check must come before int — bool is a subclass of int
        # in Python and would otherwise stringify to "True"/"False".
        if v is None or isinstance(v, str):
            return v
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        return v  # let Pydantic reject anything else
    max_length: Optional[int] = Field(
        default=None,
        description="Caps text-type fields at this many characters.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Human-readable purpose; surfaces in admin tooltips.",
    )
    enum_values: Optional[list[str]] = Field(
        default=None,
        description="Closed value set; valid only when type='text'.",
    )


class TableDefinition(BaseModel):
    """One entity ('menu_items', 'products', 'posts').

    Labels carry the human-readable shape so admin generators don't
    have to round-trip the LLM for "Menu Items" given "menu_items".
    `public_read=True` is the default because most website tables (menu,
    gallery, posts) are public-read. Set to False for admin-only data
    (orders, contact submissions).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="snake_case plural identifier, e.g. menu_items")
    singular_label: str = Field(description="e.g. 'Menu Item'")
    plural_label: str = Field(description="e.g. 'Menu Items'")
    description: str = Field(description="Why this table exists, in one sentence")
    fields: list[FieldDefinition]
    indexes: list[str] = Field(
        default_factory=list,
        description="Field names to index for read performance.",
    )
    public_read: bool = Field(
        default=True,
        description="Anon role can SELECT. True for website-facing tables.",
    )
    relationships: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Reserved for v2. v1 emitters MAY populate as hints; "
                    "the validator and DDL generator both ignore this.",
    )


class DataModel(BaseModel):
    """Full description of one project's runtime data.

    `tables` is the structured part — admin generators iterate this to
    build CRUD UIs, websites iterate this to build read queries.
    `singletons` is the unstructured part — copy that lives in
    `src/content/*.json` rather than the database (hero tagline, footer
    text, about-page paragraphs). The two are presented side-by-side so
    a single artifact captures everything the pipeline emitted about
    the project's data shape.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    tables: list[TableDefinition] = Field(default_factory=list)
    singletons: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON for content that stays out of the DB.",
    )


# ── Validation ────────────────────────────────────────────────────────

def validate_data_model(model: DataModel) -> list[str]:
    """Return a list of semantic errors. Empty list means the model is
    valid for codegen and provisioning.

    Pydantic already enforces shape and types; this catches the
    semantic rules that Pydantic doesn't express on its own:
      * snake_case for every table and field name
      * no duplicate table names across the model
      * no duplicate field names within a table
      * enum_values only present when type='text', and non-empty when set
      * `default` on a `required` field is suspicious if it's the
        literal string "NULL" (mismatch between "must be set" + "null
        when unset")
      * every `indexes` entry must reference a real field of that table
    """
    errors: list[str] = []

    table_names_seen: set[str] = set()
    for ti, table in enumerate(model.tables):
        loc = f"tables[{ti}] ({table.name!r})"

        if not _SNAKE_CASE_RE.match(table.name):
            errors.append(f"{loc}: name is not snake_case")

        if table.name in table_names_seen:
            errors.append(f"{loc}: duplicate table name")
        table_names_seen.add(table.name)

        field_names_seen: set[str] = set()
        field_name_set: set[str] = set()
        for fi, field in enumerate(table.fields):
            floc = f"{loc}.fields[{fi}] ({field.name!r})"

            if not _SNAKE_CASE_RE.match(field.name):
                errors.append(f"{floc}: name is not snake_case")

            if field.name in field_names_seen:
                errors.append(f"{floc}: duplicate field name within table")
            field_names_seen.add(field.name)
            field_name_set.add(field.name)

            if field.enum_values is not None:
                if field.type != "text":
                    errors.append(
                        f"{floc}: enum_values is only valid for type='text', "
                        f"got type={field.type!r}"
                    )
                if not field.enum_values:
                    errors.append(f"{floc}: enum_values is set but empty")

            if field.required and isinstance(field.default, str):
                if field.default.strip().upper() == "NULL":
                    errors.append(
                        f"{floc}: required field cannot default to NULL"
                    )

        for idx in table.indexes:
            if idx not in field_name_set:
                errors.append(
                    f"{loc}.indexes: {idx!r} is not a field of this table"
                )

    return errors


# ── Normalization helpers ─────────────────────────────────────────────

def normalize_field_name(name: str) -> str:
    """Convert free-form text to a snake_case column name.

    Examples
    --------
        'Menu Item Name'   → 'menu_item_name'
        'created-at'       → 'created_at'
        'Price (cents)'    → 'price_cents'
        'URL'              → 'url'
        '  ___  '          → ''   (caller should reject)

    Note: empty/garbage input yields an empty string. Callers should
    treat empty as an error rather than retrying with a fallback —
    silent fallbacks hide LLM emission bugs.
    """
    return _slugify(name)


def normalize_table_name(name: str) -> str:
    """Convert free-form text to a snake_case table name.

    Same algorithm as `normalize_field_name`. Pluralization is NOT
    applied here — emitters are expected to produce a plural form
    ('menu items'), not a singular ('menu item'). Adding heuristic
    pluralization here would mask emission bugs.
    """
    return _slugify(name)


def _slugify(raw: str) -> str:
    """Lowercase, replace non-alnum with `_`, collapse runs, strip
    leading/trailing underscores. No language-specific transliteration —
    if the LLM emits non-ASCII we'd rather catch it in validation than
    silently re-encode."""
    if not raw:
        return ""
    s = raw.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")
