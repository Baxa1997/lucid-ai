"""Test the clarity agent with diverse prompts.

Run: docker exec -it lucid-ai-ai_engine-1 python /app/test_clarity_agent.py
"""
import asyncio, sys, json
sys.path.insert(0, "/app")
from app.services.clarity_agent import check_prompt_clarity

CASES = [
    # Should ask → missing location
    ("coffee shop",                          {},                          "ASK: location"),
    ("yoga studio",                          {},                          "ASK: location or type"),
    ("restaurant",                           {},                          "ASK: location or cuisine"),

    # Should pass → enough context
    ("Italian coffee shop in Florence",      {},                          "PASS"),
    ("SaaS invoicing tool for freelancers",  {},                          "PASS"),
    ("luxury skincare brand for women 40+",  {},                          "PASS"),
    ("Korean BBQ restaurant in Manhattan",   {},                          "PASS"),

    # Should ask → ambiguous type
    ("company website",                      {},                          "ASK: page type"),
    ("portfolio",                            {},                          "ASK: type or field"),

    # Should pass → round 3 reached (max rounds)
    ("coffee shop",                          {"location": "florence", "style": "rustic", "type": "landing"}, "PASS (max rounds)"),

    # Should pass → already clarified enough
    ("coffee shop",                          {"location": "florence"},    "PASS or ASK style"),
]

async def main():
    print(f"\n{'='*70}")
    print("CLARITY AGENT TEST")
    print('='*70)

    correct = 0
    total = len(CASES)

    for prompt, already, expectation in CASES:
        result = await check_prompt_clarity(prompt, already, timeout_s=15.0)
        if result is None:
            verdict = "PASS"
            detail = "(clear — no question)"
        else:
            verdict = "ASK"
            detail = f"key={result.get('key')!r}  q={result.get('text')!r}"
            opts = [o.get('label') for o in result.get('options', [])]
            detail += f"\n     opts={opts}"

        expected_pass = expectation.startswith("PASS")
        actual_pass = result is None
        match = "✓" if expected_pass == actual_pass else "✗"
        if expected_pass == actual_pass:
            correct += 1

        print(f"\n{match} [{expectation}]")
        print(f"  prompt: {prompt!r}  already={already}")
        print(f"  → {verdict}  {detail}")

    print(f"\n{'='*70}")
    print(f"SCORE: {correct}/{total} correct")
    print('='*70)

if __name__ == "__main__":
    asyncio.run(main())
