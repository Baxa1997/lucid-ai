"""Verify gemini-3.1-pro-preview and gemini-3-flash-preview on Vertex AI.

Covers the call shapes Lucid AI's pipeline actually uses:
  - Basic generation
  - Structured JSON output (response_schema)
  - Web grounding (google_search) — pro only

Auth: gcloud ADC. Project: lucid-ai-495710.
"""

import asyncio
import json
import os
import time

os.environ["GOOGLE_CLOUD_PROJECT"] = "lucid-ai-495710"

from google import genai
from google.genai import types

PROJECT = "lucid-ai-495710"
LOCATION = "global"

PRO_MODEL = "gemini-3.1-pro-preview"
FLASH_MODEL = "gemini-3-flash-preview"

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def _ok(label: str, t0: float, extra: str = "") -> bool:
    dt = time.monotonic() - t0
    print(f"✅ {label}  ({dt:.1f}s) {extra}")
    return True


def _fail(label: str, exc: Exception) -> bool:
    print(f"❌ {label}  → {type(exc).__name__}: {exc}")
    return False


async def test_basic(model: str) -> bool:
    label = f"{model} :: basic"
    t0 = time.monotonic()
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents="Say hello in exactly one short sentence.",
            config=types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.3,
            ),
        )
        text = (resp.text or "").strip()
        return _ok(label, t0, f"→ {text[:80]!r}")
    except Exception as e:
        return _fail(label, e)


async def test_json(model: str) -> bool:
    label = f"{model} :: structured json"
    t0 = time.monotonic()
    try:
        schema = {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING"},
                "city": {"type": "STRING"},
                "specialty": {"type": "STRING"},
                "price_range": {"type": "STRING"},
            },
            "required": ["name", "city", "specialty", "price_range"],
        }
        resp = await client.aio.models.generate_content(
            model=model,
            contents=(
                "Return a JSON object describing a fictional Italian restaurant "
                "with fields: name, city, specialty, price_range."
            ),
            config=types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        parsed = json.loads(resp.text or "{}")
        keys = sorted(parsed.keys())
        return _ok(label, t0, f"→ keys={keys}")
    except Exception as e:
        return _fail(label, e)


async def test_grounding(model: str) -> bool:
    label = f"{model} :: web grounding"
    t0 = time.monotonic()
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=(
                "Find 2 real Italian restaurants in New York City. "
                "Return their names and one specific dish each is known for."
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=2000,
                temperature=0.3,
            ),
        )
        text = (resp.text or "").strip()
        sources = 0
        cand = resp.candidates[0] if resp.candidates else None
        if cand and getattr(cand, "grounding_metadata", None):
            chunks = getattr(cand.grounding_metadata, "grounding_chunks", None) or []
            sources = sum(
                1 for c in chunks
                if getattr(c, "web", None) and getattr(c.web, "uri", None)
            )
        return _ok(label, t0, f"→ {sources} sources, text[:60]={text[:60]!r}")
    except Exception as e:
        return _fail(label, e)


async def main():
    print("=" * 60)
    print(f"Vertex AI · project={PROJECT} · location={LOCATION}")
    print(f"Pro:   {PRO_MODEL}")
    print(f"Flash: {FLASH_MODEL}")
    print("=" * 60)

    results = await asyncio.gather(
        test_basic(PRO_MODEL),
        test_json(PRO_MODEL),
        test_grounding(PRO_MODEL),
        test_basic(FLASH_MODEL),
        test_json(FLASH_MODEL),
        test_grounding(FLASH_MODEL),
    )

    passed = sum(results)
    total = len(results)
    print("=" * 60)
    print(f"Results: {passed}/{total} passed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
