import asyncio
import os
import sys

# Add ai_engine to path so app.supabase_client works
sys.path.append(os.path.join(os.path.dirname(__file__), "ai_engine"))
from app.supabase_client import db_client

async def main():
    try:
        async with db_client(None) as client:
            result = await client.table("chat_sessions").select("id, project_id, platform_repo_url, created_at").order("created_at", desc=True).limit(5).execute()
            print("LATEST CHAT SESSIONS:")
            for row in result.data:
                print(row)
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    asyncio.run(main())
