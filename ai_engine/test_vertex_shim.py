"""Smoke-test gemini_http shim against Vertex AI directly.

Loads the shim by file path (skipping the heavy app package init that
needs fastapi/supabase/etc.) and exercises a Flash + JSON call —
the same payload shape used by landing_intent.py.
"""

import asyncio
import importlib.util
import json
import os
import sys
import types

# Force vertex BEFORE the shim loads
os.environ["USE_VERTEX_AI"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = "lucid-ai-495710"
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Stub `app` and `app.config` so gemini_http loads without dragging
#    in fastapi / supabase / openhands.
class _StubSettings:
    USE_VERTEX_AI = True
    GOOGLE_CLOUD_PROJECT = "lucid-ai-495710"
    GOOGLE_CLOUD_LOCATION = "global"


_stub_app = types.ModuleType("app")
_stub_config = types.ModuleType("app.config")
_stub_config.settings = _StubSettings()
sys.modules["app"] = _stub_app
sys.modules["app.config"] = _stub_config

spec = importlib.util.spec_from_file_location(
    "app.services.gemini_http",
    os.path.join(ROOT, "app/services/gemini_http.py"),
)
gemini_http = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gemini_http)


async def main():
    payload = {
        "contents": [{"parts": [{"text": "Return JSON: {\"city\": \"NYC\", \"hello\": \"world\"}"}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    print("─" * 50)
    print("Testing shim → Vertex AI (Flash, JSON)")
    print("─" * 50)
    status, data, raw = await gemini_http.gemini_post(
        model="gemini-2.5-flash",
        payload=payload,
        timeout_s=30.0,
        api_key="",
        label="shim_test",
    )
    print(f"  status: {status}")
    if status == 200 and data:
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            print(f"  text:   {text[:160]}")
            parsed = json.loads(text)
            print(f"  parsed: {parsed}")
            usage = data.get("usageMetadata") or {}
            print(f"  usage:  in={usage.get('promptTokenCount')} out={usage.get('candidatesTokenCount')}")
            print("✅ Shim → Vertex works")
        except Exception as e:
            print(f"❌ parse failed: {e}")
            print(f"   data keys: {list(data.keys())}")
    else:
        print(f"❌ status={status} raw={raw[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
