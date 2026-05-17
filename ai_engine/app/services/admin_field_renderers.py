"""Field-type → JSX snippet reference.

Pure functions that map DataModel `FieldDefinition`s to the JSX shapes
Claude is expected to emit for forms + list cells. The Claude prompts
include this output as worked examples so the model has a concrete
contract to follow.

These functions are NOT called at runtime to assemble the generated
JSX — Claude does the actual assembly. They're documentation by
construction: the test suite asserts the prompt text matches what
these functions produce, so prompt drift surfaces as a test break.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.data_model import FieldDefinition


# ── Field-type → input element ───────────────────────────────────────

def render_input_for_field(field: FieldDefinition) -> str:
    """Return the JSX `<input/>` or `<textarea/>` snippet for a field.

    The snippet expects `register("<field_name>")` from react-hook-form
    to be spread into props, but we omit the spread here — Claude
    composes it. Shape is meant to be readable by a human, not parsed.
    """
    name      = field.name
    required  = field.required
    field_type = (field.type or "text").lower()
    enum_vals  = field.enum_values
    max_len    = field.max_length

    required_attr = " required" if required else ""
    common = f'name="{name}" id="{name}"' + required_attr
    class_in = (
        ' className="w-full px-3 py-2 border border-border rounded bg-background"'
    )

    if enum_vals:
        opts = "\n        ".join(
            f'<option value="{v}">{v}</option>' for v in enum_vals
        )
        return (
            f'<select {common}{class_in}>\n'
            f'        <option value="">— select —</option>\n'
            f'        {opts}\n'
            f'      </select>'
        )

    if field_type == "boolean":
        # Checkbox doesn't accept className the same way; use a wrapper.
        return (
            f'<input type="checkbox" {common} '
            f'className="h-4 w-4 rounded border-border" />'
        )

    if field_type == "json":
        return (
            f'<textarea {common}{class_in} rows={{6}} '
            f'placeholder=\'{{"key": "value"}}\' />'
        )

    # Long text → textarea
    if field_type == "text" and (max_len or 0) > 100:
        max_attr = f' maxLength={{{max_len}}}' if max_len else ""
        return f'<textarea {common}{class_in} rows={{4}}{max_attr} />'

    type_attr = {
        "email":      "email",
        "phone":      "tel",
        "url":        "url",
        "image_url":  "url",
        "number":     "number",
        "integer":    "number",
        "date":       "date",
        "datetime":   "datetime-local",
    }.get(field_type, "text")
    extra = ""
    if field_type == "number":
        extra = ' step="any"'
    elif field_type == "integer":
        extra = ' step="1"'
    if max_len and field_type in ("text", "email", "phone", "url", "image_url"):
        extra += f' maxLength={{{max_len}}}'
    return f'<input type="{type_attr}" {common}{class_in}{extra} />'


# ── Field-type → list-cell renderer ──────────────────────────────────

def render_cell_for_field(field: FieldDefinition) -> str:
    """Return the JSX expression for a list-table cell rendering `field`.

    `row` is assumed in scope. The expressions are pure JSX —
    Claude wraps them in `<td>` cells.
    """
    name = field.name
    field_type = (field.type or "text").lower()

    if field_type == "boolean":
        return f'{{row.{name} ? "✓" : "—"}}'

    if field_type == "image_url":
        return (
            f'{{row.{name} ? <img src={{row.{name}}} alt="" '
            f'className="w-10 h-10 rounded object-cover" /> : "—"}}'
        )

    if field_type in ("date", "datetime"):
        return (
            f'{{row.{name} ? new Intl.DateTimeFormat("en-US", '
            f'{{ dateStyle: "medium" }}).format(new Date(row.{name})) : "—"}}'
        )

    if field_type in ("email", "phone", "url"):
        prefix = {"email": "mailto:", "phone": "tel:"}.get(field_type, "")
        return (
            f'{{row.{name} ? <a href={{`{prefix}${{row.{name}}}`}} '
            f'className="text-primary hover:underline">{{row.{name}}}</a> : "—"}}'
        )

    if field_type == "json":
        return f'<code className="text-xs">{{JSON.stringify(row.{name})?.slice(0, 60)}}</code>'

    # Text with truncation
    return (
        f'{{row.{name} == null ? "—" : '
        f'(String(row.{name}).length > 60 '
        f'? String(row.{name}).slice(0, 60) + "…" '
        f': row.{name})}}'
    )


# ── Field summary text for prompt bodies ─────────────────────────────

def format_fields_for_prompt(fields: list[FieldDefinition]) -> str:
    """Compact human-readable list of `name (type, [required], [enum], [maxLen])`.

    Used inline in the user prompts so Claude sees field metadata
    in one block rather than as nested JSON.
    """
    lines: list[str] = []
    for f in fields:
        bits = [f.name, f.type]
        if f.required:
            bits.append("required")
        if f.enum_values:
            bits.append(f"enum={json.dumps(f.enum_values)}")
        if f.max_length:
            bits.append(f"maxLength={f.max_length}")
        if f.description:
            bits.append(f"# {f.description}")
        lines.append("  - " + ", ".join(bits))
    return "\n".join(lines) if lines else "  (no fields)"


def format_field_examples(fields: list[FieldDefinition]) -> str:
    """Render a worked example block: for each field, show what the
    JSX input AND the list cell should look like. Included verbatim
    in the user prompts so Claude's anchor isn't just abstract rules.
    """
    blocks: list[str] = []
    for f in fields[:6]:  # cap the prompt size
        blocks.append(
            f"  • {f.name} ({f.type})\n"
            f"      input:  {render_input_for_field(f)}\n"
            f"      cell:   {render_cell_for_field(f)}"
        )
    return "\n".join(blocks) if blocks else "  (no fields)"
