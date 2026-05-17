# Data Model Format

The data contract every generated product writes — and the next generator
will read. Lives at `app/services/data_model.py`; this doc explains what
each field means and what the format looks like in practice.

Both the website pipeline and the (forthcoming) admin pipeline emit one
`DataModel` per project. Because the shape is shared, the future
"generate-admin-for-this-website" feature is mechanical: read the
website's stored `DataModel`, pass it to the admin pipeline, done.

---

## Top-level shape

```jsonc
{
  "version": "1.0",
  "tables": [
    /* TableDefinition[] — entities backed by Postgres tables */
  ],
  "singletons": {
    /* free-form JSON — copy that stays in src/content/*.json */
  }
}
```

### `tables`
Each table maps 1:1 to a Postgres table inside the project's tenant
schema (created by the tenant_provisioner). Use a table for anything
with **N rows** — menu items, products, posts, testimonials. Pluralized,
snake_case names.

### `singletons`
A free-form JSON dict for content that's **conceptually one thing** —
hero copy, footer paragraph, about-page hero image. Storing these in DB
tables is overkill (one row, edited rarely) so they live in
`src/content/landing.json` (or similar) and ship with the build. The
admin generator can edit these by patching the JSON file at write time.

---

## `TableDefinition`

| Field | Type | Notes |
|---|---|---|
| `name` | string | snake_case plural identifier (`menu_items`, `blog_posts`) |
| `singular_label` | string | UI label, e.g. `"Menu Item"` |
| `plural_label` | string | UI label, e.g. `"Menu Items"` |
| `description` | string | One-sentence purpose; surfaces in admin tooltips |
| `fields` | `FieldDefinition[]` | columns |
| `indexes` | `string[]` | field names to index (read-perf only; PK is automatic) |
| `public_read` | bool | Anon role can `SELECT`. `True` for website-facing tables. Set `False` for admin-only data (orders, contact form submissions). |
| `relationships` | `dict[]` | **Reserved for v2** — v1 accepts the slot but the validator and DDL generator both ignore it. |

---

## `FieldDefinition`

| Field | Type | Notes |
|---|---|---|
| `name` | string | snake_case column name |
| `type` | `FieldType` | closed set, see below |
| `required` | bool | maps to SQL `NOT NULL` |
| `default` | string? | SQL `DEFAULT` expression (`'pending'`, `0`, `now()`); `None` for no default |
| `max_length` | int? | caps text fields |
| `description` | string? | human-readable purpose |
| `enum_values` | `string[]?` | only valid when `type='text'`; closed value set |

### Field types

| Value | SQL hint | UI affordance |
|---|---|---|
| `text` | `TEXT` | textarea / input |
| `number` | `NUMERIC` | numeric input |
| `boolean` | `BOOLEAN` | switch |
| `date` | `DATE` | date picker |
| `datetime` | `TIMESTAMPTZ` | datetime picker |
| `json` | `JSONB` | code editor (use sparingly — kills query-ability) |
| `image_url` | `TEXT` | image upload + preview |
| `email` | `TEXT` | email input + validator |
| `phone` | `TEXT` | phone input |
| `url` | `TEXT` | url input + validator |

---

## Validation rules (`validate_data_model`)

Returns a list of error strings; empty list = valid. Pydantic itself
catches shape / type errors at construction time; the validator catches
the semantic rules:

- Every table and field name matches `^[a-z][a-z0-9_]*$`.
- No two tables share a name.
- No two fields within a table share a name.
- `enum_values` is only set when `type='text'`; when set, it must be
  non-empty.
- `required=True` + `default='NULL'` (the literal string) is rejected
  — it's a contradiction.
- Every entry in `indexes` is a real field name of that table.

---

## Example 1 — Restaurant

```jsonc
{
  "version": "1.0",
  "tables": [
    {
      "name": "menu_items",
      "singular_label": "Menu Item",
      "plural_label": "Menu Items",
      "description": "Dishes the restaurant sells.",
      "fields": [
        {"name": "name", "type": "text", "required": true, "max_length": 120},
        {"name": "description", "type": "text"},
        {"name": "price_cents", "type": "number", "required": true},
        {"name": "photo_url", "type": "image_url"},
        {"name": "category", "type": "text",
         "enum_values": ["starter", "main", "dessert", "drink"]},
        {"name": "is_available", "type": "boolean", "default": "true"}
      ],
      "indexes": ["category"],
      "public_read": true
    },
    {
      "name": "gallery_images",
      "singular_label": "Gallery Image",
      "plural_label": "Gallery",
      "description": "Photos shown on the gallery page.",
      "fields": [
        {"name": "image_url", "type": "image_url", "required": true},
        {"name": "caption", "type": "text"},
        {"name": "sort_order", "type": "number", "default": "0"}
      ],
      "public_read": true
    },
    {
      "name": "testimonials",
      "singular_label": "Testimonial",
      "plural_label": "Testimonials",
      "description": "Customer quotes for the home page.",
      "fields": [
        {"name": "author", "type": "text", "required": true},
        {"name": "role", "type": "text"},
        {"name": "quote", "type": "text", "required": true, "max_length": 500},
        {"name": "avatar_url", "type": "image_url"}
      ],
      "public_read": true
    },
    {
      "name": "reservations",
      "singular_label": "Reservation",
      "plural_label": "Reservations",
      "description": "Bookings made by guests.",
      "fields": [
        {"name": "guest_name", "type": "text", "required": true},
        {"name": "guest_email", "type": "email", "required": true},
        {"name": "party_size", "type": "number", "required": true},
        {"name": "reserved_for", "type": "datetime", "required": true},
        {"name": "status", "type": "text", "required": true, "default": "'pending'",
         "enum_values": ["pending", "confirmed", "cancelled"]}
      ],
      "indexes": ["reserved_for"],
      "public_read": false
    }
  ],
  "singletons": {
    "hero": {"title": "Golden Dragon", "tagline": "Modern Sichuan, downtown."},
    "footer": {"phone": "+1-555-0100", "address": "123 Main St"}
  }
}
```

Reservations is `public_read: false` — the website doesn't list other
guests' bookings; the admin panel reads/writes them via service-role.

---

## Example 2 — E-commerce

```jsonc
{
  "version": "1.0",
  "tables": [
    {
      "name": "categories",
      "singular_label": "Category",
      "plural_label": "Categories",
      "description": "Top-level product groupings.",
      "fields": [
        {"name": "name", "type": "text", "required": true},
        {"name": "slug", "type": "text", "required": true},
        {"name": "description", "type": "text"}
      ],
      "indexes": ["slug"],
      "public_read": true
    },
    {
      "name": "products",
      "singular_label": "Product",
      "plural_label": "Products",
      "description": "Items for sale.",
      "fields": [
        {"name": "name", "type": "text", "required": true},
        {"name": "slug", "type": "text", "required": true},
        {"name": "description", "type": "text"},
        {"name": "price_cents", "type": "number", "required": true},
        {"name": "category_slug", "type": "text",
         "description": "Soft FK to categories.slug (relationships deferred to v2)."},
        {"name": "image_url", "type": "image_url"},
        {"name": "stock_count", "type": "number", "default": "0"},
        {"name": "is_active", "type": "boolean", "default": "true"}
      ],
      "indexes": ["slug", "category_slug"],
      "public_read": true
    },
    {
      "name": "orders",
      "singular_label": "Order",
      "plural_label": "Orders",
      "description": "Customer purchases.",
      "fields": [
        {"name": "customer_email", "type": "email", "required": true},
        {"name": "customer_name", "type": "text", "required": true},
        {"name": "total_cents", "type": "number", "required": true},
        {"name": "status", "type": "text", "required": true, "default": "'pending'",
         "enum_values": ["pending", "paid", "shipped", "delivered", "refunded"]},
        {"name": "line_items", "type": "json", "required": true,
         "description": "[{product_slug, qty, price_cents}, ...]"},
        {"name": "placed_at", "type": "datetime", "required": true, "default": "now()"}
      ],
      "indexes": ["status", "customer_email"],
      "public_read": false
    }
  ],
  "singletons": {
    "hero": {"title": "Your Store", "tagline": "Curated finds for everyday."},
    "shipping": {"flat_rate_cents": 599, "free_threshold_cents": 5000}
  }
}
```

`line_items` uses `type: json` because per-order line items aren't a
collection the website reads on its own — they're nested inside the
order. Real foreign keys land in v2 (see the `relationships` slot).

---

## Example 3 — Blog

```jsonc
{
  "version": "1.0",
  "tables": [
    {
      "name": "authors",
      "singular_label": "Author",
      "plural_label": "Authors",
      "description": "People who write posts.",
      "fields": [
        {"name": "name", "type": "text", "required": true},
        {"name": "slug", "type": "text", "required": true},
        {"name": "bio", "type": "text"},
        {"name": "avatar_url", "type": "image_url"},
        {"name": "twitter_handle", "type": "text"}
      ],
      "indexes": ["slug"],
      "public_read": true
    },
    {
      "name": "categories",
      "singular_label": "Category",
      "plural_label": "Categories",
      "description": "Topic groupings for posts.",
      "fields": [
        {"name": "name", "type": "text", "required": true},
        {"name": "slug", "type": "text", "required": true}
      ],
      "indexes": ["slug"],
      "public_read": true
    },
    {
      "name": "posts",
      "singular_label": "Post",
      "plural_label": "Posts",
      "description": "Published articles.",
      "fields": [
        {"name": "title", "type": "text", "required": true, "max_length": 200},
        {"name": "slug", "type": "text", "required": true},
        {"name": "excerpt", "type": "text", "max_length": 300},
        {"name": "body_markdown", "type": "text", "required": true},
        {"name": "hero_image_url", "type": "image_url"},
        {"name": "author_slug", "type": "text",
         "description": "Soft FK to authors.slug."},
        {"name": "category_slug", "type": "text",
         "description": "Soft FK to categories.slug."},
        {"name": "published_at", "type": "datetime"},
        {"name": "status", "type": "text", "required": true, "default": "'draft'",
         "enum_values": ["draft", "published", "archived"]}
      ],
      "indexes": ["slug", "status", "published_at"],
      "public_read": true
    }
  ],
  "singletons": {
    "site": {"title": "The Blog", "tagline": "Notes on building things."},
    "footer": {"copyright": "© 2026"}
  }
}
```

---

## How websites and admins read this differently

The same `DataModel` is consumed by both, but the access pattern is
opposite.

| | Website | Admin |
|---|---|---|
| Auth role | `anon` (or `authenticated` for logged-in features) | `authenticated` + `project_members` |
| Operations | `SELECT` only | full CRUD |
| RLS gating | `public_read` policies on each table | `EXISTS (SELECT 1 FROM project_members WHERE project_id = <parent> AND user_id = auth.uid())` |
| Singletons | imports `src/content/*.json` at build time | edits the same JSON via a file-write API |
| Reads | `supabase.schema('tenant_xxx').from('menu_items').select('*').eq('is_available', true)` | `supabase.schema('tenant_xxx').from('menu_items').select('*')` (no filter) |
| Writes | never | `insert / update / delete` via service_role or member-gated anon |

Tables with `public_read: false` (orders, reservations, contact submissions)
are invisible to the website's anon client by RLS — only the admin sees them.

---

## What's deferred to v2

- **Relationships / foreign keys.** v1 emitters can use `*_slug` columns
  as soft references (see e-commerce + blog examples). The
  `relationships` slot exists on `TableDefinition` so emitters can hint
  at intended links without committing to a schema; the validator and
  DDL generator both ignore it. v2 will formalize the shape.
- **Computed / derived columns.** No `GENERATED` columns yet.
- **Unique constraints** beyond the implicit primary key.
- **Per-field RLS.** Row-level only in v1; column-level masking is a v2
  concern.
- **Migrations within a project.** Once a table is provisioned, schema
  drift is on the operator. The admin generator can produce a fresh
  table but won't `ALTER` an existing one in v1.
