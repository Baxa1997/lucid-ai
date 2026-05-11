"""Chain smoke test: expand_page_brief → page_codegen.generate_page.

Verifies the slice-2 contract:
  1. expand_page_brief turns sparse {section_types: [...]} into filled sections
  2. Each filled section matches its schema shape
  3. page_codegen reads those exact fields (no shape drift)

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_expand_page_chain_smoke.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.expand_page_brief import expand_page_brief
from app.services.page_codegen import generate_page
from app.services.section_schemas import schema_for, canonical_type


# ── hand-built sparse project brief (what project_brief.py would emit) ──

PROJECT_BRIEF = {
    "brand": {"name": "Aerial"},
    "industry": "B2B SaaS",
    "primary_purpose": "signup",
    "audience": {"primary": "Engineering managers at 10-200 person SaaS companies"},
    "personality": {
        "tone": "confident",
        "vibe_keywords": ["modern", "clear", "trustworthy"],
        "energy": "medium",
    },
    "voice": {
        "sample": "We move fast on code, careful on data. Our customers trust us with their pipeline."
    },
    "palette": {
        "primary": "240 100% 60%",
        "primary-foreground": "0 0% 100%",
        "background": "0 0% 100%",
        "foreground": "240 10% 4%",
        "muted": "240 5% 96%",
        "muted-foreground": "240 4% 46%",
        "border": "240 6% 90%",
        "card": "0 0% 100%",
        "accent": "240 5% 96%",
        "accent-foreground": "240 6% 10%",
        "radius": "0.5rem",
    },
    "typography": {"heading": "Inter", "body": "Inter"},
    "design_system": {"radius": "0.5rem", "spacing_scale": "default"},
}

# A single page from project_brief.pages[] — sparse, only structure hints.
PAGE = {
    "slug": "pricing",
    "title": "Pricing",
    "nav_label": "Pricing",
    "page_goal": "Help engineering managers compare plans and start a free trial.",
    "primary_cta": {"label": "Start free trial", "href": "/signup"},
    "section_types": ["hero", "pricing_table", "faq", "cta_block"],
}


# ── Validation helpers ──────────────────────────────────────────────


def validate_section_shape(section: dict, canon_type: str) -> list[str]:
    """Return list of issues with this section vs its schema."""
    issues: list[str] = []
    schema = schema_for(canon_type) or {}

    def check(filled, sch, path: str) -> None:
        if isinstance(sch, list):
            sub = sch[0] if sch else None
            if not isinstance(filled, list):
                issues.append(f"{path}: expected list, got {type(filled).__name__}")
                return
            for i, item in enumerate(filled):
                if sub is not None:
                    check(item, sub, f"{path}[{i}]")
            return
        if isinstance(sch, dict):
            if not isinstance(filled, dict):
                issues.append(f"{path}: expected dict, got {type(filled).__name__}")
                return
            for k, v in sch.items():
                if k not in filled:
                    issues.append(f"{path}.{k}: missing")
                else:
                    check(filled[k], v, f"{path}.{k}")
            return
        # Leaf: schema is a description string, filled should be scalar.
        # Empty strings are OK (schema explicitly allows them).

    check(section, schema, canon_type)
    return issues


# ── Main ────────────────────────────────────────────────────────────


async def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    print("─" * 78)
    print("STAGE 1 — expand_page_brief (Gemini Flash)")
    print("─" * 78)
    print(f"Page: {PAGE['title']}, requested types: {PAGE['section_types']}")

    t0 = time.time()
    page_brief = await expand_page_brief(
        page=PAGE,
        project_brief=PROJECT_BRIEF,
        websocket=None,
        timeout_s=90.0,
    )
    t1 = time.time()
    print(f"⏱  {t1 - t0:.1f}s")

    if page_brief is None:
        print("FAIL — expand_page_brief returned None")
        sys.exit(1)

    sections = page_brief.get("sections") or []
    print(f"sections returned: {len(sections)}")
    for s in sections:
        print(f"  • {s.get('id')} — fields: {list(s.keys())[:8]}")

    # Validate each section against its schema.
    print("\n── Schema validation ──")
    total_issues = 0
    for s in sections:
        canon = canonical_type(s.get("type") or s.get("id") or "")
        issues = validate_section_shape(s, canon)
        if issues:
            total_issues += len(issues)
            print(f"  ⚠ {canon}: {len(issues)} issues")
            for it in issues[:5]:
                print(f"    - {it}")
        else:
            print(f"  ✓ {canon}")
    if total_issues:
        print(f"\nFAIL — {total_issues} schema issues")
        # Don't sys.exit yet — continue to Claude stage to see the chain end-to-end
        # but mark the failure.

    # Persist the expanded brief for inspection.
    out_path = "/tmp/test_expand_page_brief.json"
    with open(out_path, "w") as fh:
        json.dump(page_brief, fh, indent=2, ensure_ascii=False)
    print(f"  expanded brief saved to {out_path}")

    # ── STAGE 2 — page_codegen ─────────────────────────────────────
    print("\n" + "─" * 78)
    print("STAGE 2 — page_codegen.generate_page (Claude)")
    print("─" * 78)

    page_meta = page_brief["page_meta"]
    t2 = time.time()
    result = await generate_page(
        page_meta=page_meta,
        sections=sections,
        brand_name=PROJECT_BRIEF["brand"]["name"],
        motif="minimal",
        palette=PROJECT_BRIEF["palette"],
        typography=PROJECT_BRIEF["typography"],
        design_system=PROJECT_BRIEF["design_system"],
        personality=PROJECT_BRIEF["personality"],
        references=None,
        design_tokens=None,
        voice_context=None,
        api_key=api_key,
        websocket=None,
    )
    t3 = time.time()
    print(f"⏱  {t3 - t2:.1f}s")

    if result is None:
        print("FAIL — generate_page returned None")
        sys.exit(1)

    files = result.get("files") or []
    print(f"page_slug:  {result['page_slug']!r}")
    print(f"route_path: {result['route_path']!r}")
    print(f"files:      {len(files)} total")

    out_dir = "/tmp/test_expand_page_chain_smoke"
    os.makedirs(out_dir, exist_ok=True)
    print("\n── files ──")
    for f in files:
        path = f["path"]
        size = len(f["content"])
        print(f"  {path} ({size:,} chars)")
        rel = path.lstrip("/").replace("/", "__")
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(f["content"])

    # ── STAGE 3 — Cross-validate: does Claude's JSX read fields Gemini wrote? ─
    print("\n── Cross-validation: Claude reads what Gemini wrote ──")
    cross_issues = 0
    # Build a {canonical_type → jsx} map by parsing component imports in the route.
    # Each section file is named <Slug><Type>Section.jsx and contains the section
    # component for one canonical type. Match by checking which section's content
    # fields appear in each file (more robust than name-parsing).
    section_files = [f for f in files if f["path"].startswith("src/components/sections/")]
    for sec in sections:
        canon = canonical_type(sec.get("type") or sec.get("id") or "")
        content_keys = [k for k in sec.keys() if k not in ("id", "type")]
        # Find the file whose content best matches this section's keys.
        best_file = None
        best_score = -1
        for sf in section_files:
            jsx = sf["content"]
            score = sum(
                1 for k in content_keys
                if f"content.{k}" in jsx or f"content?.{k}" in jsx
            )
            if score > best_score:
                best_score = score
                best_file = sf
        if best_file is None:
            cross_issues += 1
            print(f"  ⚠ {canon}: no matching section file")
            continue
        jsx = best_file["content"]
        present = [k for k in content_keys if (f"content.{k}" in jsx or f"content?.{k}" in jsx)]
        # `body`, `secondary_cta`, `image` are genuinely optional — JSX may
        # render them only when non-empty, so absence is not a defect.
        OPTIONAL = {"body", "secondary_cta", "image", "subheadline"}
        required_keys = [k for k in content_keys if k not in OPTIONAL]
        present_required = [k for k in required_keys if k in present]
        ratio = len(present_required) / max(1, len(required_keys))
        marker = "✓" if ratio >= 0.7 else "⚠"
        print(
            f"  {marker} {canon} → {best_file['path'].rsplit('/', 1)[-1]}: "
            f"{len(present_required)}/{len(required_keys)} required fields ({ratio:.0%})"
        )
        if ratio < 0.7:
            absent = [k for k in required_keys if k not in present]
            cross_issues += 1
            print(f"     missing: {absent[:5]}")

    # Summary.
    print("\n" + "═" * 78)
    print(f"STAGE 1 (Gemini): {t1 - t0:.1f}s   STAGE 2 (Claude): {t3 - t2:.1f}s")
    print(f"Schema issues:    {total_issues}")
    print(f"Cross-val issues: {cross_issues}")
    if total_issues == 0 and cross_issues == 0:
        print("PASS")
    else:
        print("PARTIAL — review issues above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
