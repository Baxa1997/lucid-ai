import asyncio, os, json
from app.services.claude_service import fix_bug

class MockWebSocket:
    def __init__(self):
        self.turns = 0
        
    async def send_json(self, data):
        self.turns += 1
        print(f"\n--- Turn {self.turns} [Action: {data.get('action')}] ---")
        print(data.get("content"))

async def main():
    ws = MockWebSocket()
    # A real local repo path for testing:
    repo_path = "/Users/bahriddinnurullaev/Desktop/Saas Generator Project/lucid-ai/frontend"
    # Provide an API key here or via env var
    api_key = os.getenv("ANTHROPIC_API_KEY", "your-api-key-here")
    
    print(f"Testing fix_bug in {repo_path}...")
    await fix_bug("change the button color to blue", repo_path, api_key, ws)
    print(f"\nFinished! Total turns used: {ws.turns}")

if __name__ == "__main__":
    asyncio.run(main())
