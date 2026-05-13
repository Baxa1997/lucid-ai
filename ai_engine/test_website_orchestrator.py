"""Unit tests for website_orchestrator using monkeypatched generators.

Validates:
- Parallel dispatch of pages + header + footer
- Concurrency cap enforcement
- Failure isolation (one page failing doesn't kill others)
- Result aggregation (files, failed_routes, page_results, header/footer ok)

Run: docker exec lucid-ai-ai_engine-1 python /app/test_website_orchestrator.py
"""
import asyncio
import sys
sys.path.insert(0, "/app")


# ── Monkeypatch the generators with fakes ────────────────────────────
# Done BEFORE importing website_orchestrator so the imports inside
# generate_website resolve to our fakes.
class FakeContext:
    """Module-level state for fakes — track call counts + concurrency."""
    page_calls: list[str] = []
    header_calls = 0
    footer_calls = 0
    concurrent: int = 0
    max_concurrent: int = 0
    fail_routes: set[str] = set()
    fail_header: bool = False
    fail_footer: bool = False


def reset_ctx():
    FakeContext.page_calls = []
    FakeContext.header_calls = 0
    FakeContext.footer_calls = 0
    FakeContext.concurrent = 0
    FakeContext.max_concurrent = 0
    FakeContext.fail_routes = set()
    FakeContext.fail_header = False
    FakeContext.fail_footer = False


async def fake_generate_one_page(*, page, visual_dna, brand_name, tagline, domain, api_key, websocket=None):
    FakeContext.concurrent += 1
    FakeContext.max_concurrent = max(FakeContext.max_concurrent, FakeContext.concurrent)
    route = page.get("route") or "/"
    FakeContext.page_calls.append(route)
    await asyncio.sleep(0.05)  # simulate Claude latency
    FakeContext.concurrent -= 1
    if route in FakeContext.fail_routes:
        return None
    slug = "home" if route == "/" else route.strip("/").replace("/", "-")
    return [
        {"path": f"src/app/{'' if slug=='home' else slug+'/'}page.js",
         "content": f"// {slug} page"},
        {"path": f"src/components/pages/{slug}/Hero.jsx",
         "content": f"// {slug} hero"},
    ]


async def fake_generate_header(*, brand_name, tagline, domain, visual_dna, api_key, websocket=None):
    FakeContext.concurrent += 1
    FakeContext.max_concurrent = max(FakeContext.max_concurrent, FakeContext.concurrent)
    FakeContext.header_calls += 1
    await asyncio.sleep(0.05)
    FakeContext.concurrent -= 1
    if FakeContext.fail_header:
        return None
    return {"path": "src/components/layout/MarketingHeader.jsx", "content": "// header"}


async def fake_generate_footer(*, brand_name, tagline, domain, visual_dna, api_key, websocket=None):
    FakeContext.concurrent += 1
    FakeContext.max_concurrent = max(FakeContext.max_concurrent, FakeContext.concurrent)
    FakeContext.footer_calls += 1
    await asyncio.sleep(0.05)
    FakeContext.concurrent -= 1
    if FakeContext.fail_footer:
        return None
    return {"path": "src/components/layout/MarketingFooter.jsx", "content": "// footer"}


# Monkeypatch — must happen before website_orchestrator imports inside generate_website
import app.services.page_generator as _pg
import app.services.header_footer_generator as _hfg
_pg.generate_one_page = fake_generate_one_page
_hfg.generate_header = fake_generate_header
_hfg.generate_footer = fake_generate_footer

from app.services.website_orchestrator import generate_website


PLAN = {
    "brand": {"name": "TestBrand", "tagline": "Tag", "domain": "food"},
    "pages": [
        {"route": "/",        "title": "Home",    "sections": [{"type": "hero"}]},
        {"route": "/menu",    "title": "Menu",    "sections": [{"type": "menu"}]},
        {"route": "/about",   "title": "About",   "sections": [{"type": "story"}]},
        {"route": "/contact", "title": "Contact", "sections": [{"type": "contact"}]},
    ],
}
VD = {"cultural_intensity": "bold"}


async def test_happy_path():
    reset_ctx()
    res = await generate_website(
        plan=PLAN, visual_dna=VD, api_key="fake", concurrency=4,
    )
    assert res["header_ok"] is True, "header should succeed"
    assert res["footer_ok"] is True, "footer should succeed"
    assert res["failed_routes"] == [], f"no failed routes; got {res['failed_routes']}"
    assert all(v for v in res["page_results"].values()), "all pages should pass"
    # 4 pages × 2 files + header + footer = 10 files
    assert len(res["files"]) == 10, f"expected 10 files, got {len(res['files'])}"
    # All 4 pages were called
    assert set(FakeContext.page_calls) == {"/", "/menu", "/about", "/contact"}
    # Concurrency cap respected
    assert FakeContext.max_concurrent <= 4, f"max_concurrent={FakeContext.max_concurrent}"
    print(f"  ✓ happy_path: 10 files, max_concurrent={FakeContext.max_concurrent}")


async def test_one_page_fails():
    reset_ctx()
    FakeContext.fail_routes = {"/about"}
    res = await generate_website(
        plan=PLAN, visual_dna=VD, api_key="fake", concurrency=4,
    )
    assert res["header_ok"] is True
    assert res["footer_ok"] is True
    assert res["failed_routes"] == ["/about"], f"expected ['/about'], got {res['failed_routes']}"
    assert res["page_results"]["/about"] is False
    assert res["page_results"]["/"] is True
    # 3 pages × 2 files + header + footer = 8 files
    assert len(res["files"]) == 8, f"expected 8 files, got {len(res['files'])}"
    print(f"  ✓ one_page_fails: 8 files, failed=[/about]")


async def test_header_fails_pages_still_ok():
    reset_ctx()
    FakeContext.fail_header = True
    res = await generate_website(
        plan=PLAN, visual_dna=VD, api_key="fake", concurrency=4,
    )
    assert res["header_ok"] is False
    assert res["footer_ok"] is True
    assert res["failed_routes"] == []
    # 4 pages × 2 files + footer (no header) = 9 files
    assert len(res["files"]) == 9, f"expected 9 files, got {len(res['files'])}"
    print(f"  ✓ header_fails_pages_ok: header_ok=False, pages all passed")


async def test_concurrency_high():
    """Verify true parallelism — with concurrency=8 and 6 tasks, max should be 6."""
    reset_ctx()
    plan6 = {
        "brand": PLAN["brand"],
        "pages": [
            {"route": f"/page{i}", "sections": [{"type": "hero"}]}
            for i in range(6)
        ],
    }
    res = await generate_website(
        plan=plan6, visual_dna=VD, api_key="fake", concurrency=8,
    )
    # 6 pages + header + footer = 8 total tasks, all parallel
    assert FakeContext.max_concurrent >= 6, f"max_concurrent={FakeContext.max_concurrent}"
    assert len(res["page_results"]) == 6
    print(f"  ✓ concurrency_high: 8 tasks parallel, max_concurrent={FakeContext.max_concurrent}")


async def test_concurrency_cap():
    """With concurrency=2 and 6 tasks, max should be exactly 2."""
    reset_ctx()
    plan6 = {
        "brand": PLAN["brand"],
        "pages": [
            {"route": f"/page{i}", "sections": [{"type": "hero"}]}
            for i in range(6)
        ],
    }
    await generate_website(
        plan=plan6, visual_dna=VD, api_key="fake", concurrency=2,
    )
    assert FakeContext.max_concurrent <= 2, f"max_concurrent={FakeContext.max_concurrent}"
    print(f"  ✓ concurrency_cap: max_concurrent={FakeContext.max_concurrent} (cap=2)")


async def test_skip_header_footer():
    reset_ctx()
    res = await generate_website(
        plan=PLAN, visual_dna=VD, api_key="fake",
        concurrency=4, skip_header=True, skip_footer=True,
    )
    assert FakeContext.header_calls == 0
    assert FakeContext.footer_calls == 0
    assert res["header_ok"] is False
    assert res["footer_ok"] is False
    # 4 pages × 2 files = 8
    assert len(res["files"]) == 8
    print(f"  ✓ skip_header_footer: 8 files, no chrome calls")


async def test_empty_plan():
    res = await generate_website(
        plan={"brand": {"name": "X"}, "pages": []},
        visual_dna={}, api_key="fake",
    )
    assert res["files"] == []
    assert res["page_results"] == {}
    print(f"  ✓ empty_plan: returns empty result, no exception")


async def main():
    print("="*72)
    print("WEBSITE ORCHESTRATOR — UNIT TESTS")
    print("="*72)
    tests = [
        test_happy_path,
        test_one_page_fails,
        test_header_fails_pages_still_ok,
        test_concurrency_high,
        test_concurrency_cap,
        test_skip_header_footer,
        test_empty_plan,
    ]
    passed = 0
    for t in tests:
        try:
            await t()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            print(f"  ✗ {t.__name__} threw: {e}")
    print(f"\nSCORE: {passed}/{len(tests)}")
    print("="*72)


if __name__ == "__main__":
    asyncio.run(main())
