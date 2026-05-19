"""Claude prompt: per-entity admin CREATE form.

Produces a Client Component with one form input per field. Submitting
calls `createRow` (migration 026 RPC) and redirects to the list view.

Field-type → input element mapping is enforced by the prompt body
(it injects worked examples from `admin_field_renderers`). Claude is
expected to use those shapes verbatim rather than improvising.
"""
from __future__ import annotations

from app.services.admin_field_renderers import (
    format_fields_for_prompt,
    format_field_examples,
)
from app.services.data_model import TableDefinition
from app.services.prompts._visual_context import (
    VISUAL_CONTEXT_RULE,
    build_visual_context_block,
)


_SYSTEM_PROMPT = """\
You are generating a React/Vite component for an admin panel CREATE form.
The user fills out one input per field, the form submits via createRow,
and on success redirects to the list page. Routing is React Router, not
Next.js.

OUTPUT REQUIREMENTS:
- Single .jsx file, no TypeScript anywhere
- Use Tailwind classes only
- Use react-hook-form's useForm + register for state + validation
- Import { useNavigate } from "react-router-dom"
- Import from "@/lib/db_admin.js": createRow
- Import AuthGuard from "@/components/AuthGuard.jsx"
- Component must start with the "use client" directive
- Wrap the entire return in <AuthGuard>
- Show submit button "Saving…" while pending
- On submit success: call the function returned by useNavigate()
- On submit failure: show error message below form

FIELD-TYPE → INPUT MAPPING (use exactly):
- text                          → <input type="text">
- text + enum_values            → <select> with one <option> per enum value
- text + max_length > 100       → <textarea>
- email                         → <input type="email">
- phone                         → <input type="tel">
- url / image_url               → <input type="url">
- number                        → <input type="number" step="any">
- integer                       → <input type="number" step="1">
- boolean                       → <input type="checkbox">
- date                          → <input type="date">
- datetime                      → <input type="datetime-local">
- json                          → <textarea> + JSON.parse before submit

VALIDATION:
- required fields: pass { required: true } to register()
- max_length:      pass { maxLength: <n> } to register()
- json:            wrap JSON.parse in try/catch, surface "Invalid JSON" inline
- boolean:         convert checkbox value to true/false on submit
- number/integer:  Number() the value before submit; empty → undefined

FORBIDDEN:
- No fetch() — use createRow helper
- No direct supabase or @supabase/* imports
- No next/link or next/navigation imports
- No TypeScript syntax (no `: Type`, no `as`, no <T>)
- No external form libraries other than react-hook-form (already installed)
- No `id`, `created_at`, or `updated_at` fields in the form payload —
  Postgres assigns those automatically.

Return ONLY the file content for the requested path. The tool you
must call to respond accepts a list of files; emit exactly one file.
""" + "\n" + VISUAL_CONTEXT_RULE + "\n"


def build_create_view_prompt(
    entity: TableDefinition,
    admin_plan: dict,
) -> dict[str, str]:
    branding = (admin_plan or {}).get("branding") or {}
    brand_name = branding.get("brand_name") or "Admin"

    slug = entity.name.replace("_", "-")
    fields_block = format_fields_for_prompt(entity.fields)
    examples_block = format_field_examples(entity.fields)
    visual_context = build_visual_context_block(admin_plan)

    user_prompt = f"""\
Generate the CREATE form for the "{entity.name}" entity.

{visual_context}
ENTITY:       {entity.singular_label or entity.name}
TABLE:        {entity.name}
DESCRIPTION:  {entity.description}
BRAND:        {brand_name}

FIELDS (excluding id/created_at/updated_at which Postgres handles):
{fields_block}

PER-FIELD INPUT EXAMPLES (use these JSX shapes; substitute names as needed):
{examples_block}

ROUTES:
  this form:    /{slug}/new
  list (after submit): /{slug}

REQUIRED STRUCTURE:

  "use client";
  import {{ useState }} from "react";
  import {{ useNavigate }} from "react-router-dom";
  import {{ useForm }} from "react-hook-form";
  import {{ createRow }} from "@/lib/db_admin.js";
  import {{ AuthGuard }} from "@/components/AuthGuard.jsx";

  export default function {entity.name.title().replace("_", "")}NewPage() {{
    const navigate = useNavigate();
    const {{ register, handleSubmit, formState: {{ errors }} }} = useForm();
    const [submitting, setSubmitting] = useState(false);
    const [error, setError]           = useState(null);

    async function onSubmit(data) {{
      setSubmitting(true);
      setError(null);
      try {{
        // Coerce types — checkbox -> bool, number inputs -> Number,
        // json textarea -> JSON.parse.
        await createRow("{entity.name}", data);
        navigate("/{slug}");
      }} catch (e) {{
        setError(e.message || String(e));
        setSubmitting(false);
      }}
    }}

    return (
      <AuthGuard>
        <div className="p-8 max-w-2xl">
          {{/* heading, form, fields, error, submit button */}}
        </div>
      </AuthGuard>
    );
  }}

Generate src/pages/{entity.name.title().replace("_", "")}Create.jsx. Return ONLY that one file's content.
"""

    return {"system": _SYSTEM_PROMPT, "user": user_prompt}
