import os
import asyncio

os.environ["GOOGLE_CLOUD_PROJECT"] = "lucid-ai-495710"

import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    Tool,
    grounding,
    GenerationConfig,
)

vertexai.init(project="lucid-ai-495710", location="global")


async def test_basic():
    print("\n🧪 Test 1: Basic call...")
    try:
        model = GenerativeModel(
            "gemini-3.1-pro-preview",
            generation_config=GenerationConfig(
                max_output_tokens=100,
                temperature=0.3,
            ),
        )
        response = await model.generate_content_async("Say hello in one sentence")
        print(f"✅ Basic call works: {response.text.strip()}")
        return True
    except Exception as e:
        print(f"❌ Basic call failed: {e}")
        return False


async def test_grounding():
    print("\n🧪 Test 2: Web grounding call (google-genai SDK)...")
    try:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(
            vertexai=True, project="lucid-ai-495710", location="global"
        )
        response = await client.aio.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=(
                "Find 2 real Italian restaurants in New York City. "
                "Return their name and one specific dish they are known for."
            ),
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                max_output_tokens=2000,
                temperature=0.3,
            ),
        )
        text = response.text or ""
        print(f"✅ Grounding works!")
        print(f"   Response: {text[:300]}")

        sources = []
        cand = response.candidates[0] if response.candidates else None
        if cand and getattr(cand, "grounding_metadata", None):
            metadata = cand.grounding_metadata
            chunks = getattr(metadata, "grounding_chunks", None) or []
            sources = [
                c.web.uri for c in chunks
                if getattr(c, "web", None) and getattr(c.web, "uri", None)
            ]
        print(f"   Sources found: {len(sources)}")
        return True
    except Exception as e:
        print(f"❌ Grounding call failed: {e}")
        return False


async def test_flash():
    print("\n🧪 Test 3: Gemini Flash call...")
    try:
        model = GenerativeModel(
            "gemini-3.1-flash-lite",
            generation_config=GenerationConfig(
                max_output_tokens=50,
                temperature=0.1,
            ),
        )
        response = await model.generate_content_async(
            "What type of project is this? Reply in one word: "
            "'landing_page' or 'website'. Prompt: 'build me a coffee shop website'"
        )
        print(f"✅ Flash works: {response.text.strip()}")
        return True
    except Exception as e:
        print(f"❌ Flash call failed: {e}")
        return False


async def test_json_output():
    print("\n🧪 Test 4: JSON structured output...")
    try:
        model = GenerativeModel(
            "gemini-3.1-pro-preview",
            generation_config=GenerationConfig(
                max_output_tokens=2000,
                temperature=0.1,
            ),
        )
        response = await model.generate_content_async(
            """Return a JSON object for an Italian restaurant with these fields:
            - name (string)
            - city (string)
            - specialty (string)
            - price_range (string)

            Output ONLY valid JSON. No markdown, no backticks."""
        )

        import json
        text = response.text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(text)
        print(f"✅ JSON output works: {parsed}")
        return True
    except Exception as e:
        print(f"❌ JSON output failed: {e}")
        return False


async def main():
    print("=" * 50)
    print("🚀 Vertex AI Connection Test")
    print("   Project: lucid-ai-495710")
    print("   Model: gemini-3.1-pro-preview")
    print("=" * 50)

    results = await asyncio.gather(
        test_basic(),
        test_flash(),
        test_json_output(),
        test_grounding(),
    )

    passed = sum(results)
    total = len(results)

    print("\n" + "=" * 50)
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("✅ Vertex AI fully working!")
        print("   Safe to integrate into your pipeline.")
    elif passed >= 2:
        print("⚠️  Partial success - check failed tests above")
    else:
        print("❌ Connection failed - check gcloud auth")
        print("   Run: gcloud auth application-default login")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
