"""Test the deterministic homepage builder.

Run: docker exec -it lucid-ai-ai_engine-1 python /app/test_homepage_builder.py
"""
import sys
sys.path.insert(0, "/app")
from app.services.homepage_builder import plan_homepage

# Realistic project schemas
SCHEMA_COFFEE = {
    "brand": {"name": "Caffè Verona", "tagline": "Italian artisan coffee in Florence"},
    "sections": [
        {"type": "hero", "headline": "Authentic Italian coffee"},
        {"type": "menu", "headline": "Our blends"},
        {"type": "story", "headline": "Three generations of roasters"},
        {"type": "gallery"},
        {"type": "testimonials"},
        {"type": "contact"},
    ],
}

SCHEMA_SAAS = {
    "brand": {"name": "InvoiceFlow", "tagline": "Invoicing for freelancers"},
    "sections": [
        {"type": "hero"},
        {"type": "features"},
        {"type": "pricing"},
        {"type": "testimonials"},
        {"type": "cta"},
    ],
}

SCHEMA_PORTFOLIO = {
    "brand": {"name": "Jane Doe Design"},
    "sections": [
        {"name": "Hero"},
        {"name": "Work"},
        {"name": "Services"},
        {"name": "About"},
        {"name": "Contact"},
    ],
}

SCHEMA_WITH_EXPLICIT_COMPONENTS = {
    "brand": {"name": "Brand X"},
    "sections": [
        {"type": "hero", "component": "BigHero"},
        {"type": "features", "component": "FeatureGrid"},
        {"type": "cta", "component": "CallToAction"},
    ],
}

SCHEMA_EMPTY = {"brand": {"name": "Empty"}, "sections": []}
SCHEMA_NO_SECTIONS = {"brand": {"name": "No Sections"}}

CASES = [
    ("Italian coffee shop", SCHEMA_COFFEE),
    ("SaaS invoicing tool", SCHEMA_SAAS),
    ("Designer portfolio", SCHEMA_PORTFOLIO),
    ("Explicit components", SCHEMA_WITH_EXPLICIT_COMPONENTS),
    ("Empty sections", SCHEMA_EMPTY),
    ("No sections key", SCHEMA_NO_SECTIONS),
]


def main():
    print("="*72)
    print("HOMEPAGE BUILDER TEST")
    print("="*72)
    ok = 0
    for label, schema in CASES:
        print(f"\n── {label} ──")
        plan = plan_homepage(schema, stack="nextjs")
        if plan is None:
            print("  → None (caller falls back to Claude)")
            if not schema.get("sections"):
                ok += 1
                print("  ✓ Expected None for empty/missing sections")
            continue
        comps = [s["component"] for s in plan["sections"]]
        print(f"  path: {plan['rel_path']}")
        print(f"  components: {comps}")
        print(f"  contents preview ({len(plan['contents'])} chars):")
        for line in plan["contents"].split("\n")[:6]:
            print(f"    {line}")
        print("    ...")
        ok += 1

    print(f"\n{'='*72}")
    print(f"SCORE: {ok}/{len(CASES)}")
    print('='*72)


if __name__ == "__main__":
    main()
