# Tenant RPCs — read and write paths for per-project data

Every Lucid AI project that has a `data_model` (Phase 2.3 onwards)
also gets its own Postgres schema named `tenant_<first-12-hex-of-uuid>`.
Tables in that schema are NOT directly reachable through PostgREST —
Supabase only exposes schemas listed in its static `db_schemas` config
(`public`, `graphql_public`). Tenant schemas are dynamic per project,
so they can never be on that list.

The bridge is a small set of public RPCs under `public.*`. Each one
takes the `project_id`, validates auth + the requested table against
the project's stored `data_model`, then runs a single SQL statement
against the right tenant schema with `SECURITY DEFINER` elevation.

| RPC | Read/Write | Auth required | Returns | Migration |
|---|---|---|---|---|
| [`get_tenant_collection`](#get_tenant_collection) | read (public) | anon or authenticated | JSONB array of rows | 025 |
| [`get_tenant_collection_authenticated`](#get_tenant_collection_authenticated) | read (admin) | **authenticated only** | JSONB array of rows | 027 |
| [`set_tenant_row`](#set_tenant_row) | write (insert) | **authenticated only** | JSONB (the inserted row) | 026 |
| [`update_tenant_row`](#update_tenant_row) | write (patch) | **authenticated only** | JSONB (the updated row) | 026 |
| [`delete_tenant_row`](#delete_tenant_row) | write (delete) | **authenticated only** | VOID | 026 |

## Auth model

- **Anon callers (generated websites)** can ONLY hit
  `get_tenant_collection`, and only for tables marked `public_read=true`
  in the project's `data_model`. They cannot enumerate tables, write,
  or read non-public tables.
- **Authenticated callers (admin panel users, dashboard users)**
  resolve to an `auth.uid()` which both the write RPCs AND the
  authenticated-read RPC match against
  [`public.project_members`](../../supabase/migrations/020_project_members.sql).
  Project members can read EVERY table in the project's `data_model`
  (including ones marked `public_read=false`) via
  `get_tenant_collection_authenticated`, and can write via the
  three write RPCs.
- **Service role (server-side ai_engine)** bypasses all checks. Only
  used during pipeline runs and admin operations.

Membership is checked via the existing
[`public.is_project_member(p_project_id, p_user_id)`](../../supabase/migrations/020_project_members.sql#L67-L78)
helper — itself `SECURITY DEFINER STABLE` so it stays cheap and
RLS-recursion-free.

## Error codes

All error messages are short snake_case strings raised via
`RAISE EXCEPTION`. They're chosen so the admin UI can map them
1:1 to user-facing messages.

| Error | When | HTTP-equivalent | RPCs that raise it |
|---|---|---|---|
| `access_denied` | `auth.uid()` is NULL, or user is not in `project_members` for `p_project_id` | 403 | get_authenticated, set, update, delete |
| `project_not_found` | `chat_sessions` row missing | 404 | get_authenticated, set, update, delete |
| `no_tenant` | The (resolved) `chat_sessions` row has NULL `tenant_schema` — unprovisioned, or a linked admin whose parent is missing/unprovisioned | 404 | get_authenticated |
| `invalid_table` | `p_table_name` failed regex shape check OR not in this project's `data_model.tables` | 400 | get_authenticated, set, update, delete |
| `invalid_order_column` | `p_order_by` is NULL or fails the identifier regex (`^[a-z_][a-z0-9_]*$`) | 400 | get_authenticated |
| `invalid_order_direction` | `p_order_direction` is not `asc`/`desc` (case-insensitive) | 400 | get_authenticated |
| `invalid_limit` | `p_limit` is NULL, `<1`, or `>1000` | 400 | get_authenticated |
| `invalid_offset` | `p_offset` is NULL or `<0` | 400 | get_authenticated |
| `invalid_payload` | `set_tenant_row` got a `p_payload` with zero writable columns (no overlap with the table) | 400 | set |
| `row_not_found` | `update_tenant_row` or `delete_tenant_row` targeted an id that doesn't exist | 404 | update, delete |
| _(Postgres native)_ | NOT NULL / CHECK / type-cast failures from the underlying INSERT or UPDATE | varies | set, update |

Native Postgres errors are NOT remapped — if a CHECK constraint
fails the caller receives the original PG error code (`23514` for
check violation, `23502` for NOT NULL, etc.). That's intentional:
the admin UI can show the column-specific message.

---

## `get_tenant_collection`

**Migration:** 025
**Signature:** `(p_project_id UUID, p_table_name TEXT, p_limit INT DEFAULT 200) → JSONB`
**Grant:** `anon, authenticated, service_role`

Returns up to `p_limit` rows (hard cap 500) from the named table.
The table must be `public_read=true` in the project's `data_model`,
otherwise returns `[]` (uniform "not found" behaviour for anon).

```js
// Generated website's src/lib/db.js calls it like this:
const { data } = await supabase.rpc("get_tenant_collection", {
  p_project_id: PROJECT_ID,
  p_table_name: "menu_items",
  p_limit: 200,
});
// data = [{ id, name, price_cents, ... }, ...]
```

---

## `get_tenant_collection_authenticated`

**Migration:** 027
**Signature:** `(p_project_id UUID, p_table_name TEXT, p_order_by TEXT DEFAULT 'created_at', p_order_direction TEXT DEFAULT 'desc', p_limit INT DEFAULT 100, p_offset INT DEFAULT 0) → JSONB`
**Grant:** `authenticated, service_role`

The authenticated counterpart to `get_tenant_collection`. Lets a
project member read **every** table in the project's `data_model`,
including ones marked `public_read=false`. Adds explicit
`ORDER BY` + `LIMIT/OFFSET` arguments since admin grids paginate.

Why a separate RPC instead of widening `get_tenant_collection`:
the anon RPC deliberately returns `[]` for non-public tables — so
generated websites can't even enumerate the existence of internal
tables. Admins need the opposite. Different security model →
different RPC.

```js
// Generated admin panel's src/lib/db.js calls it like this:
const { data } = await supabase.rpc("get_tenant_collection_authenticated", {
  p_project_id:      PROJECT_ID,
  p_table_name:      "orders",
  p_order_by:        "created_at",
  p_order_direction: "desc",
  p_limit:           50,
  p_offset:          0,
});
// data = [{ id, status, total_cents, customer_email, … }, …]
```

Failure modes:
- `access_denied` — not authenticated, or not a member of `p_project_id`.
- `project_not_found` — no `chat_sessions` row with that id.
- `no_tenant` — the project (or its parent, if linked) has NULL
  `tenant_schema`. Render a "not yet provisioned" state.
- `invalid_table` — table not in this project's effective `data_model`.
- `invalid_order_column` / `invalid_order_direction` /
  `invalid_limit` / `invalid_offset` — argument shape guards. These
  fire BEFORE any DB read so a malicious caller can't waste a
  chat_sessions lookup with junk identifiers.

### Parent-linking semantics

An admin panel is provisioned as its own `chat_sessions` row whose
`parent_project_id` points at the website project it manages
(see migration 023's `parent_project_id` column,
[`resolve_tenant_for_project`](../app/services/pipeline_tenant.py)).
When this RPC sees a non-NULL `parent_project_id`, it resolves
`tenant_schema` and `data_model` from the parent row — so the admin
reads the SAME tenant data the website does.

**The membership check is still performed against the CALLING
`p_project_id`, NEVER against the parent.** An admin panel has its
own `project_members` list — separate from the website it manages.
A user invited only to the admin can read tenant data; a user
invited only to the website cannot. This is by design — websites
are publishing surfaces, admins are operating surfaces, and they
have independent permission boundaries.

### What this RPC does NOT do (v1)

- No `WHERE` clause / filter support. Pagination + `ORDER BY` only.
  Adding filters means sanitising operator + value types per
  column; out of scope for v1.
- No `JOIN`s. The data_model has no relationships in v1 anyway (see
  [`DATA_MODEL_FORMAT.md`](DATA_MODEL_FORMAT.md)). When relationships
  ship we can add a `get_tenant_collection_with_joins` sibling.
- No multi-table fetch in one call. Generated admin pages issue one
  RPC per table — the per-call cost is negligible and the
  per-table cache invalidation story is much simpler.

---

## `set_tenant_row`

**Migration:** 026
**Signature:** `(p_project_id UUID, p_table_name TEXT, p_payload JSONB) → JSONB`
**Grant:** `authenticated, service_role`

Inserts ONE row. Payload keys outside the table's declared columns
are silently dropped (defensive against admin UI drift). The
reserved server-managed columns `id`, `created_at`, `updated_at`
are ALWAYS stripped from the payload so their `DEFAULT` clauses
fire.

```js
const { data: row } = await supabase.rpc("set_tenant_row", {
  p_project_id: PROJECT_ID,
  p_table_name: "menu_items",
  p_payload:    {
    name:         "Margherita",
    price_cents:  1200,
    description:  "Classic tomato + mozzarella",
    is_available: true,
  },
});
// row.id, row.created_at populated by Postgres defaults.
```

Failure modes:
- `access_denied` — not authenticated, or not in `project_members`.
- `project_not_found` — `chat_sessions` row missing or unprovisioned.
- `invalid_table` — table not in this project's `data_model`.
- `invalid_payload` — no column in the payload matches the table.
- Native Postgres errors propagate (e.g. `23502` NOT NULL).

---

## `update_tenant_row`

**Migration:** 026
**Signature:** `(p_project_id UUID, p_table_name TEXT, p_row_id UUID, p_payload JSONB) → JSONB`
**Grant:** `authenticated, service_role`

PATCH semantics — only fields in `p_payload` are touched; unspecified
columns retain their existing values. `id`, `created_at`,
`updated_at` are reserved and cannot be overridden (the
`set_updated_at` trigger handles `updated_at` automatically).

```js
const { data: row } = await supabase.rpc("update_tenant_row", {
  p_project_id: PROJECT_ID,
  p_table_name: "menu_items",
  p_row_id:     "3ae614ec-379f-…",
  p_payload:    { price_cents: 1300 },
});
// row.price_cents === 1300; everything else unchanged.
```

Failure modes:
- All from `set_tenant_row` (same auth + table validation), plus:
- `row_not_found` — no row with that id in the target table.

Edge case: if `p_payload` is empty or contains only non-existent
columns, the call still validates auth + table, then returns the
current row unchanged (or raises `row_not_found` if the id doesn't
exist). No INSERT-style "no writable columns" error in this path —
treat an empty patch as a no-op fetch.

---

## `delete_tenant_row`

**Migration:** 026
**Signature:** `(p_project_id UUID, p_table_name TEXT, p_row_id UUID) → VOID`
**Grant:** `authenticated, service_role`

Hard delete. No soft-delete column today — admin UIs that need
trash/restore semantics must add it explicitly to the data_model.

```js
await supabase.rpc("delete_tenant_row", {
  p_project_id: PROJECT_ID,
  p_table_name: "menu_items",
  p_row_id:     "3ae614ec-379f-…",
});
// VOID — Supabase returns { data: null, error: null } on success.
```

Failure modes: same as update — `row_not_found` on a missing id.

---

## Why three functions instead of one

A polymorphic `mutate_tenant_row(op, ...)` was considered but
rejected:

- Each operation has different argument shapes (insert has no row id;
  update needs both id and payload; delete only needs id).
- They have different return types (VOID for delete vs JSONB).
- Future audit/rate-limit hooks per operation are easier when the
  function name in `pg_stat_statements` already tells you what
  happened.

## Why `SECURITY DEFINER` and not RLS-only

Tenant tables already have RLS policies (generated by
[`tenant_sql_generator.rls_policies_for_table`](../app/services/tenant_sql_generator.py))
scoped to the project's membership. So why not just give authenticated
users direct PostgREST access?

Answer: PostgREST cannot expose tenant schemas (see the introduction).
Without these RPCs there is no path from a browser to a tenant table.
The RPC IS the access layer. Inside the RPC, `SECURITY DEFINER`
elevates so it can `EXECUTE format(...)` across schemas; the
`is_project_member` check on the way in is what enforces isolation —
not RLS on the underlying tables (which the elevation bypasses).

## Local development gotcha

`auth.uid()` returns NULL when calling through the service-role client
(`SUPABASE_SERVICE_KEY`), so writing tests that need to assert auth
behaviour requires minting a real JWT signed with `SUPABASE_JWT_SECRET`
and sending it via the anon-key client. See
[`tests/test_set_tenant_row.py:_mint_user_jwt`](../tests/test_set_tenant_row.py)
and
[`tests/test_get_tenant_collection_authenticated.py:_mint_user_jwt`](../tests/test_get_tenant_collection_authenticated.py).

## Applying / re-applying migrations

The Phase 2/3 RPC migrations (025, 026, 027) all use
`CREATE OR REPLACE FUNCTION`, so they're safely idempotent. The
applier scripts live under `ai_engine/scripts/`:

- `apply_migration_026.py` — write RPCs
- `apply_migration_027.py` — authenticated-read RPC

Both share the same PL/pgSQL-aware SQL splitter at
[`app/services/sql_splitter.py`](../app/services/sql_splitter.py) —
naive `sql.split(';')` would shred function bodies + comments that
contain `;`. Each script reads SQL from stdin (since the
`supabase/migrations/` directory is not bind-mounted into the
container), applies each top-level statement via the
service-role `execute_ddl` RPC (migration 024), and runs a
functional probe to confirm the function is callable. After
applying, both also fire `NOTIFY pgrst, 'reload schema'` so
PostgREST picks up the new function immediately (instead of
waiting for its ~30s auto-reload).

```bash
# Pattern (same for 026 and 027):
cat supabase/migrations/027_get_tenant_collection_authenticated.sql \
  | docker exec -i lucid-ai-ai_engine-1 \
      python scripts/apply_migration_027.py
```
