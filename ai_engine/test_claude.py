import asyncio
from app.services.pipeline import execute_with_claude

async def main():
    classifica = {"model_id": "anthropic/claude-sonnet-4-6", "max_turns": 3}
    api_key = "test_key"
    workspace = "/tmp/lucid_conv_test_123"
    import os
    os.makedirs(workspace, exist_ok=True)
    with open(f"{workspace}/test_file.js", "w") as f:
        f.write("console.log(\"hello\");")
        
    async for msg in execute_with_claude(
        task="Can you edit test_file.js and replace hello with world?",
        classification=classifica,
        api_key=api_key,
        workspace_path=workspace,
        session_id="test_123"
    ):
        print(msg)

if __name__ == "__main__":
    asyncio.run(main())
