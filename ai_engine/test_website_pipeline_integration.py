"""End-to-end integration test for the website pipeline.

Runs Stages 1-7 against a real temp workspace with Stage 6 (Claude calls)
MOCKED with valid mock output. This validates:
  - Stage 5 foundation files actually get written to disk correctly
  - Stage 6 file paths from mocked Claude calls are written correctly
  - Stage 7 verification catches REAL issues (or passes when files are valid)
  - The full pipeline returns True (success) when everything is wired right

No Claude tokens needed — all Claude calls are monkeypatched.
Gemini stages 1-4 DO run (need Vertex ADC credentials).

Run: docker exec lucid-ai-ai_engine-1 python /app/test_website_pipeline_integration.py
"""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/app")


# ── Mock the Claude-side generators BEFORE importing website_pipeline ──
def _good_jsx(name: str) -> str:
    return f'''export default function {name}() {{
  return (
    <section className="py-24 md:py-32">
      <div className="max-w-7xl mx-auto px-4 md:px-8">
        <h1 className="font-heading text-5xl tracking-tight">{name}</h1>
        <p className="font-body text-lg leading-relaxed">Mock content for {name}.</p>
      </div>
    </section>
  );
}}
'''


async def mock_generate_one_page(*, page, visual_dna, brand_name, tagline, domain, api_key, websocket=None, page_images=None, **_kw):
    """Return realistic mock files mirroring what Claude would return."""
    route = (page.get("route") or "/").strip()
    slug = "home" if route == "/" else route.strip("/").replace("/", "-")
    page_file = "src/app/page.js" if slug == "home" else f"src/app/{slug}/page.js"

    # Build component names from page_generator's naming scheme
    from app.services.page_generator import _section_component_name
    sections = page.get("sections") or []
    comps = [_section_component_name(slug, s.get("type") or "section") for s in sections]

    files = []
    imports = "\n".join(f'import {c} from "@/components/pages/{slug}/{c}";' for c in comps)
    body = "\n".join(f"      <{c} />" for c in comps)
    files.append({
        "path": page_file,
        "content": f'{imports}\n\nexport default function HomePage() {{\n  return (\n    <main>\n{body}\n    </main>\n  );\n}}\n',
    })
    for c in comps:
        files.append({
            "path": f"src/components/pages/{slug}/{c}.jsx",
            "content": _good_jsx(c),
        })
    return files


async def mock_generate_header(*, brand_name, tagline, domain, visual_dna, api_key, websocket=None):
    return {
        "path": "src/components/layout/MarketingHeader.jsx",
        "content": _good_jsx("MarketingHeader"),
    }


async def mock_generate_footer(*, brand_name, tagline, domain, visual_dna, api_key, websocket=None):
    return {
        "path": "src/components/layout/MarketingFooter.jsx",
        "content": _good_jsx("MarketingFooter"),
    }


# Monkeypatch BEFORE importing website_pipeline (which imports these inline)
import app.services.page_generator as _pg
import app.services.header_footer_generator as _hfg
_pg.generate_one_page = mock_generate_one_page
_hfg.generate_header = mock_generate_header
_hfg.generate_footer = mock_generate_footer

from app.services.website_pipeline import run_website_pipeline


async def test_full_pipeline(prompt: str) -> dict:
    """Run the full pipeline against a fresh temp workspace.

    Returns a dict with diagnostic info about the run.
    """
    print(f"\n{'═'*72}\nPROMPT: {prompt}\n{'═'*72}")

    workspace = tempfile.mkdtemp(prefix="lucid_pipeline_test_")
    print(f"workspace: {workspace}")

    classification = {"layout_archetype": "consumer_website", "domain": "general"}
    validated = {"anthropic_api_key": "fake-key-mocked"}

    try:
        # Run the pipeline end-to-end (Gemini stages 1-4 real, Stage 6 mocked)
        ok = await run_website_pipeline(
            description=prompt,
            classification=classification,
            workspace_path=workspace,
            validated=validated,
            websocket=None,
            chat_session_id="",
        )

        # Inventory what got written
        all_files = []
        for root, dirs, files in os.walk(workspace):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), workspace)
                all_files.append(rel)

        foundation_files = [f for f in all_files if f.startswith(("src/config/", "src/lib/"))]
        page_files = [f for f in all_files if f.startswith("src/app/")]
        section_files = [f for f in all_files if f.startswith("src/components/pages/")]
        chrome_files = [f for f in all_files if f.startswith("src/components/layout/")]

        print(f"\n  ok                : {ok}")
        print(f"  total files       : {len(all_files)}")
        print(f"  foundation files  : {len(foundation_files)} — {foundation_files}")
        print(f"  page files        : {len(page_files)} — {page_files}")
        print(f"  section files     : {len(section_files)}")
        print(f"  chrome files      : {len(chrome_files)} — {chrome_files}")

        # Validation checks
        checks = {
            "pipeline_returned_true": ok is True,
            "site_config_exists": "src/config/site.js" in all_files,
            "navigation_exists": "src/config/navigation.js" in all_files,
            "design_system_exists": "src/lib/design-system.js" in all_files,
            "globals_css_exists": "src/app/globals.css" in all_files,
            "homepage_exists": "src/app/page.js" in all_files,
            "header_exists": "src/components/layout/MarketingHeader.jsx" in all_files,
            "footer_exists": "src/components/layout/MarketingFooter.jsx" in all_files,
            "has_section_components": len(section_files) >= 4,
            "has_inner_route_shells": any(f != "src/app/page.js" and f.endswith("page.js") for f in page_files),
        }

        # Read a section file to verify content quality
        if section_files:
            sample_path = os.path.join(workspace, section_files[0])
            with open(sample_path) as f:
                sample_content = f.read()
            checks["sample_section_has_default_export"] = "export default" in sample_content
            checks["sample_section_uses_tailwind"] = "className=" in sample_content
            checks["sample_section_no_css_vars"] = "var(--color-" not in sample_content

        all_passed = all(checks.values())
        for name, val in checks.items():
            sigil = "✓" if val else "✗"
            print(f"  {sigil} {name}")

        return {
            "prompt": prompt, "ok": all_passed,
            "total_files": len(all_files),
            "checks": checks,
        }

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def main():
    print("="*72)
    print("WEBSITE PIPELINE — END-TO-END INTEGRATION")
    print("(Gemini stages live, Stage 6 Claude calls mocked)")
    print("="*72)

    prompts = [
        "Italian coffee shop website in Florence",
        "SaaS invoicing tool website for freelance developers",
    ]

    results = []
    for p in prompts:
        try:
            r = await test_full_pipeline(p)
        except Exception as e:
            import traceback
            traceback.print_exc()
            r = {"prompt": p, "ok": False, "err": str(e)}
        results.append(r)

    print(f"\n{'═'*72}\nSUMMARY\n{'═'*72}")
    passed = sum(1 for r in results if r.get("ok"))
    for r in results:
        if r.get("ok"):
            print(f"  ✓ {r['prompt']}  ({r['total_files']} files written)")
        else:
            failed = [k for k, v in (r.get("checks") or {}).items() if not v]
            print(f"  ✗ {r['prompt']}  failed: {failed or r.get('err', 'unknown')}")
    print(f"\nSCORE: {passed}/{len(results)}")
    print("="*72)


if __name__ == "__main__":
    asyncio.run(main())
