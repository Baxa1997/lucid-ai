"""Test the fully-dynamic clarity agent across all 5 question types.

Run: docker exec -it lucid-ai-ai_engine-1 python /app/test_clarity_agent.py
"""
import asyncio, sys
sys.path.insert(0, "/app")
from app.services.clarity_agent import check_prompt_clarity

# (prompt, already_clarified, expected_key_or_PASS)
CASES = [
    # ── Location (fast-path + Gemini) ───────────────────────────────────────
    ("gym",                                    {},                          "location"),
    ("spa",                                    {},                          "location"),
    ("bakery",                                 {},                          "location"),
    ("hair salon",                             {},                          "location"),
    ("nightclub",                              {},                          "location"),
    # Already has location → PASS
    ("Italian coffee shop in Florence",        {},                          "PASS"),
    ("sushi restaurant in Tokyo",              {},                          "PASS"),
    ("gym in New York",                        {},                          "PASS"),
    ("dentist in Berlin",                      {},                          "PASS"),

    # ── Project type ────────────────────────────────────────────────────────
    ("company website",                        {},                          "project_type"),
    ("portfolio",                              {},                          "project_type"),
    ("agency",                                 {},                          "project_type"),
    # Type clear but location still missing → ask location
    ("landing page for a coffee shop",         {},                          "location"),
    ("SaaS invoicing tool for freelancers",    {},                          "PASS"),
    ("AI writing assistant app",               {},                          "PASS"),

    # ── Niche ───────────────────────────────────────────────────────────────
    ("restaurant",                             {"location": "france"},      "niche"),
    ("studio",                                 {"location": "japan"},       "niche"),

    # ── Audience ────────────────────────────────────────────────────────────
    ("clinic",                                 {},                          "audience"),
    ("fitness app",                            {},                          "PASS"),  # audience obvious

    # ── Max rounds → always PASS ────────────────────────────────────────────
    ("gym",       {"location":"italy","project_type":"landing","niche":"x"}, "PASS"),

    # ── Location answered → ask project_type next ──────────────────────────
    ("coffee shop", {"location": "italy"},                                  "project_type"),
]

async def main():
    ok = 0
    total = len(CASES)

    print(f"\n{'='*72}")
    print("CLARITY AGENT — DYNAMIC TEST")
    print('='*72)

    for prompt, already, expected in CASES:
        result = await check_prompt_clarity(prompt, already, timeout_s=15.0)

        actual_key = result["key"] if result else "PASS"
        expected_pass = expected == "PASS"
        actual_pass = result is None
        match = "✓" if expected_pass == actual_pass else "✗"
        if match == "✓":
            ok += 1

        ctx = f" already={list(already.keys())}" if already else ""
        detail = f"→ key={result['key']!r}  q={result['text']!r}" if result else "→ (clear)"
        print(f"{match} [{expected:14s}]  {prompt!r:42s}{ctx}")
        print(f"           {detail}")

    print(f"\n{'='*72}")
    print(f"SCORE: {ok}/{total}")
    print('='*72)

if __name__ == "__main__":
    asyncio.run(main())
