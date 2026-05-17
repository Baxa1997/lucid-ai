"""Claude prompt: per-entity admin LIST view.

Produces a Client Component that fetches every row in a tenant
table via `listCollection` (migration 027 RPC), renders them in a
basic data table with Edit/Delete actions, and provides an "Add new"
button linking to the create form.

What this prompt does NOT ask for (deferred):
  • Filtering / search / pagination controls
  • Bulk selection / bulk delete
  • Column-level sort
  • Inline editing
  • Sticky headers or virtualised rows
"""
from __future__ import annotations

from app.services.admin_field_renderers import (
    format_fields_for_prompt,
    format_field_examples,
)
from app.services.data_model import TableDefinition


_SYSTEM_PROMPT = """\
You are generating a React 18 / Next.js 14 App Router component for an
admin panel LIST view. The component lists rows from a Supabase table
and lets the user create / edit / delete them.

OUTPUT REQUIREMENTS:
- Single .jsx file, no TypeScript anywhere
- Use Tailwind classes only (no inline styles, no CSS modules)
- Use lucide-react for icons (Plus, Pencil, Trash2)
- Import from "@/lib/db_admin.js": listCollection, deleteRow
- Import AuthGuard from "@/components/AuthGuard.jsx"
- Import EmptyState from "@/components/EmptyState.jsx"
- Import EntityListSkeleton from "@/components/EntityListSkeleton.jsx"
- Component must start with the "use client" directive
- Wrap the entire return in <AuthGuard>
- Show <EntityListSkeleton /> while loading
- Show <EmptyState label="<plural>" createRoute="/<slug>/new" /> when 0 rows
- Show an error banner when the fetch throws
- Date fields: format with `new Intl.DateTimeFormat("en-US", {dateStyle: "medium"})`
- Boolean fields: show "✓" or em-dash
- image_url fields: render a 40x40 <img> with `className="w-10 h-10 rounded object-cover"`
- email / phone / url: render as <a href> links (mailto:/tel: prefixes for email/phone)
- Long text: truncate to 60 chars + ellipsis
- Use the deleteRow helper; gate behind window.confirm("Delete this row?")

FORBIDDEN:
- No fetch() — use listCollection helper
- No direct supabase or @supabase/* imports — db_admin abstracts that
- No external date libraries (date-fns, dayjs) — use Intl.DateTimeFormat
- No state-management libraries (redux, zustand) — useState is enough
- No TypeScript syntax (no `: Type` annotations, no `as` casts, no <T>)
- No 'await' at module top level — always inside an async function
- No global side-effects outside the component

Return ONLY the file content for the requested path. The tool you
must call to respond accepts a list of files; emit exactly one file.
"""


def build_list_view_prompt(
    entity: TableDefinition,
    admin_plan: dict,
) -> dict[str, str]:
    """Return ``{"system": ..., "user": ...}`` for the list-view codegen call.

    `admin_plan` is read only for `branding.brand_name`. The entity
    itself drives every other prompt detail.
    """
    branding = (admin_plan or {}).get("branding") or {}
    brand_name = branding.get("brand_name") or "Admin"

    slug = entity.name.replace("_", "-")
    fields_block = format_fields_for_prompt(entity.fields)
    examples_block = format_field_examples(entity.fields)

    user_prompt = f"""\
Generate the LIST view for the "{entity.name}" entity.

ENTITY:       {entity.singular_label or entity.name} \
(plural: {entity.plural_label or entity.name})
TABLE:        {entity.name}
DESCRIPTION:  {entity.description}
BRAND:        {brand_name}

FIELDS:
{fields_block}

PER-FIELD JSX EXAMPLES (use these shapes verbatim where applicable):
{examples_block}

ROUTES:
  list:  /{slug}
  new:   /{slug}/new
  edit:  /{slug}/<row.id>     (Next.js dynamic segment is /[id])

REQUIRED STRUCTURE OF THE GENERATED FILE:

  "use client";
  import {{ useEffect, useState }} from "react";
  import Link from "next/link";
  import {{ Plus, Pencil, Trash2 }} from "lucide-react";
  import {{ listCollection, deleteRow }} from "@/lib/db_admin.js";
  import {{ AuthGuard }} from "@/components/AuthGuard.jsx";
  import {{ EmptyState }} from "@/components/EmptyState.jsx";
  import {{ EntityListSkeleton }} from "@/components/EntityListSkeleton.jsx";

  export default function {entity.name.title().replace("_", "")}ListPage() {{
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    async function refresh() {{
      setLoading(true);
      setError(null);
      try {{
        const data = await listCollection("{entity.name}");
        setRows(data);
      }} catch (e) {{
        setError(e.message || String(e));
      }} finally {{
        setLoading(false);
      }}
    }}

    useEffect(() => {{ refresh(); }}, []);

    async function handleDelete(id) {{
      if (!window.confirm("Delete this row?")) return;
      try {{
        await deleteRow("{entity.name}", id);
        await refresh();
      }} catch (e) {{
        setError(e.message || String(e));
      }}
    }}

    return (
      <AuthGuard>
        <div className="p-8">
          {{/* Header + Add button */}}
          {{/* Loading: <EntityListSkeleton /> */}}
          {{/* Empty:   <EmptyState label="<plural>" createRoute="/{slug}/new" /> */}}
          {{/* Table:   <table> with row.id key, Pencil link to edit, Trash2 button */}}
        </div>
      </AuthGuard>
    );
  }}

Generate src/app/{slug}/page.jsx. Return ONLY that one file's content.
"""

    return {"system": _SYSTEM_PROMPT, "user": user_prompt}
