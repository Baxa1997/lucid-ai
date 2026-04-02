import asyncio
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "ai_engine"))
from app.supabase_client import db_client

async def main():
    try:
        async with db_client(None) as client:
            result = await client.table("chat_sessions").select("id, project_id, title, platform_repo_url").not_.is_("platform_repo_url", "null").limit(10).execute()
            print("SESSIONS WITH REPO URL:")
            for row in result.data:
                print(row)
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    asyncio.run(main())
