"""Smoke test: resolve_llm + LiteLLM Vertex routing.

Builds a Vertex Gemini Flash LLM via the same path used by openhands_manager
for clone/push agents, then issues a tiny completion to verify ADC + LiteLLM
Vertex routing is healthy.

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_resolve_llm_smoke.py
"""
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.llm import resolve_llm
from openhands.sdk.llm.message import Message, TextContent


def main() -> None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or "global"
    print(f"Vertex project: {project or '(unset)'} location={location}")

    print("Building LLM via resolve_llm('vertex_ai/gemini-3-flash-preview')")
    llm = resolve_llm("vertex_ai/gemini-3-flash-preview")
    print(f"  model: {llm.model}")

    messages = [
        Message(
            role="user",
            content=[TextContent(text="Reply with exactly the word: VERTEX_OK")],
        ),
    ]
    print("Issuing tiny completion…")
    t0 = time.time()
    resp = llm.completion(messages=messages, max_tokens=20, temperature=0)
    elapsed = time.time() - t0
    print(f"⏱  {elapsed:.1f}s")

    text = ""
    try:
        for part in resp.message.content:
            if getattr(part, "text", None):
                text += part.text
    except Exception as e:
        print(f"  could not extract content: {e!r}")
        print(f"  raw: {resp!r}")
        sys.exit(1)

    print(f"  response: {text!r}")
    if "VERTEX_OK" in (text or ""):
        print("PASS — Vertex LiteLLM round-trip OK")
    else:
        print("WARN — completion returned but did not echo VERTEX_OK")
        print("       (still confirms auth + routing succeeded)")


if __name__ == "__main__":
    main()
