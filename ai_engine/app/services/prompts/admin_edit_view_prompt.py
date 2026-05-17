"""Claude prompt: per-entity admin EDIT form.

Same shape as the create form, but:
  • loads existing row by id on mount via `listCollection` + filter
  • pre-fills form values via react-hook-form's `defaultValues`
  • renders a 404-style "Not found" state if the id doesn't match
  • submits via `updateRow` (migration 026 RPC)
  • exposes a Delete button at the bottom (with confirm)

The id is read from Next.js's `params.id` via the page-component
signature.
"""
from __future__ import annotations

from app.services.admin_field_renderers import (
    format_fields_for_prompt,
    format_field_examples,
)
from app.services.data_model import TableDefinition


_SYSTEM_PROMPT = """\
You are generating a React 18 / Next.js 14 App Router component for an
admin panel EDIT form. The page reads the row id from `params.id`,
loads the row, pre-fills the form, and lets the user save or delete.

OUTPUT REQUIREMENTS:
- Single .jsx file, no TypeScript anywhere
- Use Tailwind classes only
- Use react-hook-form: useForm({ defaultValues }) once row is loaded
- Import from "@/lib/db_admin.js": listCollection, updateRow, deleteRow
- Import AuthGuard from "@/components/AuthGuard.jsx"
- Component receives `{ params }` and reads params.id
- Component must start with the "use client" directive
- Wrap the entire return in <AuthGuard>
- While the row is loading, render a generic "Loading…" block
- If the row is not found (filter returned no match), render a
  "Not found" message with a back link to the list page
- On submit success: useRouter().push("/<entity-slug>")
- Delete button: window.confirm + deleteRow + redirect to list

FIELD-TYPE → INPUT MAPPING (use exactly):
- text                          → <input type="text">
- text + enum_values            → <select>
- text + max_length > 100       → <textarea>
- email                         → <input type="email">
- phone                         → <input type="tel">
- url / image_url               → <input type="url">
- number                        → <input type="number" step="any">
- integer                       → <input type="number" step="1">
- boolean                       → <input type="checkbox">
- date                          → <input type="date">
- datetime                      → <input type="datetime-local">
- json                          → <textarea> with JSON.parse on submit

PRE-FILL NORMALISATION:
- date/datetime fields: server returns ISO strings; the input expects
  yyyy-MM-dd (date) or yyyy-MM-ddTHH:mm (datetime-local). Slice as
  needed before assigning defaultValue.
- json fields: stringify the row's parsed JSON into the textarea.

FORBIDDEN:
- No fetch() — use listCollection / updateRow / deleteRow helpers
- No direct supabase or @supabase/* imports
- No TypeScript syntax
- No external form libraries other than react-hook-form
- No id / created_at / updated_at fields in the form

Return ONLY the file content for the requested path. The tool you
must call to respond accepts a list of files; emit exactly one file.
"""


def build_edit_view_prompt(
    entity: TableDefinition,
    admin_plan: dict,
) -> dict[str, str]:
    branding = (admin_plan or {}).get("branding") or {}
    brand_name = branding.get("brand_name") or "Admin"

    slug = entity.name.replace("_", "-")
    fields_block = format_fields_for_prompt(entity.fields)
    examples_block = format_field_examples(entity.fields)

    user_prompt = f"""\
Generate the EDIT form for the "{entity.name}" entity.

ENTITY:       {entity.singular_label or entity.name}
TABLE:        {entity.name}
DESCRIPTION:  {entity.description}
BRAND:        {brand_name}

FIELDS (excluding id/created_at/updated_at):
{fields_block}

PER-FIELD INPUT EXAMPLES:
{examples_block}

ROUTES:
  this form: /{slug}/[id]
  list:      /{slug}

ROW LOOKUP:
  In v1 the admin RPC doesn't support WHERE clauses; we read the
  whole collection and filter client-side. With a tight LIMIT this
  is fine for the row volumes admins manage. listCollection accepts
  {{ limit }} — pass {{ limit: 1000 }} and find by id.

REQUIRED STRUCTURE:

  "use client";
  import {{ useEffect, useState }} from "react";
  import {{ useRouter }} from "next/navigation";
  import {{ useForm }} from "react-hook-form";
  import {{ listCollection, updateRow, deleteRow }} from "@/lib/db_admin.js";
  import {{ AuthGuard }} from "@/components/AuthGuard.jsx";

  export default function {entity.name.title().replace("_", "")}EditPage({{ params }}) {{
    const id     = params.id;
    const router = useRouter();
    const [row, setRow]               = useState(null);
    const [loading, setLoading]       = useState(true);
    const [error, setError]           = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const {{ register, handleSubmit, reset, formState: {{ errors }} }} = useForm();

    useEffect(() => {{
      let cancelled = false;
      (async () => {{
        try {{
          const rows = await listCollection("{entity.name}", {{ limit: 1000 }});
          const match = rows.find((r) => r.id === id);
          if (cancelled) return;
          setRow(match || null);
          if (match) {{
            // Normalise date / json / boolean fields before reset()
            reset(match);
          }}
        }} catch (e) {{
          if (!cancelled) setError(e.message || String(e));
        }} finally {{
          if (!cancelled) setLoading(false);
        }}
      }})();
      return () => {{ cancelled = true; }};
    }}, [id, reset]);

    async function onSubmit(data) {{
      setSubmitting(true); setError(null);
      try {{
        await updateRow("{entity.name}", id, data);
        router.push("/{slug}");
      }} catch (e) {{
        setError(e.message || String(e));
        setSubmitting(false);
      }}
    }}

    async function handleDelete() {{
      if (!window.confirm("Delete this row?")) return;
      try {{
        await deleteRow("{entity.name}", id);
        router.push("/{slug}");
      }} catch (e) {{
        setError(e.message || String(e));
      }}
    }}

    return (
      <AuthGuard>
        <div className="p-8 max-w-2xl">
          {{/* loading state, not-found state, form, delete button, error */}}
        </div>
      </AuthGuard>
    );
  }}

Generate src/app/{slug}/[id]/page.jsx. Return ONLY that one file's content.
"""

    return {"system": _SYSTEM_PROMPT, "user": user_prompt}
