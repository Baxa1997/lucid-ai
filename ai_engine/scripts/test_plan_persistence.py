"""Smoke test for the plan-persistence helpers.

Probes the chat_sessions.pending_plan column to confirm migration 019 is
applied, then runs a save → get → clear cycle against a real chat_session
row.

Run inside the ai_engine container:
    docker exec lucid-ai-ai_engine-1 python /app/scripts/test_plan_persistence.py
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.project_generator import (
    save_persisted_plan,
    get_persisted_plan,
    clear_persisted_plan,
    _persisted_plans,
)
from app.supabase_client import db_client


async def probe_column() -> tuple[bool, str]:
    """Check whether the pending_plan column exists by selecting it."""
    try:
        async with db_client(None) as sb:
            res = await (
                sb.table("chat_sessions")
                .select("id, pending_plan")
                .limit(1)
                .execute()
            )
        return True, f"column exists, {len(res.data or [])} sample row(s)"
    except Exception as exc:
        msg = str(exc)
        if "pending_plan" in msg.lower() or "column" in msg.lower():
            return False, f"column missing — apply migration 019: {msg[:160]}"
        return False, f"unexpected DB error: {msg[:200]}"


async def find_test_session() -> str | None:
    """Pick an existing chat_session row to use as the test bed."""
    try:
        async with db_client(None) as sb:
            res = await (
                sb.table("chat_sessions")
                .select("id")
                .limit(1)
                .execute()
            )
        if res.data:
            return res.data[0]["id"]
    except Exception:
        pass
    return None


async def roundtrip(chat_session_id: str) -> tuple[bool, str]:
    """save → get → clear, verify each step."""
    # Wipe any prior state for a clean baseline
    _persisted_plans.pop(chat_session_id, None)

    plan_data = {"steps": ["build hero", "wire nav"], "pages": [{"path": "/"}]}
    await save_persisted_plan(chat_session_id, plan_data, task="smoke test task")

    # In-memory should be set
    if chat_session_id not in _persisted_plans:
        return False, "in-memory cache not set after save"

    # Drop in-memory and re-read — proves DB read works
    _persisted_plans.pop(chat_session_id, None)
    fetched = await get_persisted_plan(chat_session_id)
    if not fetched:
        return False, "DB read returned None after save (migration likely missing)"
    if fetched.get("plan_data", {}).get("pages", [{}])[0].get("path") != "/":
        return False, f"DB read returned wrong content: {fetched}"

    # Clear and re-read — should be None
    await clear_persisted_plan(chat_session_id)
    if chat_session_id in _persisted_plans:
        return False, "in-memory cache not cleared"
    fetched_after = await get_persisted_plan(chat_session_id)
    if fetched_after is not None:
        return False, f"DB still returned plan after clear: {fetched_after}"

    return True, "save → get → clear roundtrip OK"


async def main():
    print("=" * 70)
    print("Plan persistence smoke test")
    print("=" * 70)

    # Step 1 — probe column
    print("\n▶ Step 1: Probe chat_sessions.pending_plan column")
    ok, msg = await probe_column()
    print(f"  {'✅' if ok else '❌'} {msg}")
    if not ok:
        print("\n⚠️  Migration 019_pending_plan.sql is not applied.")
        print("   In-memory persistence still works, but ai_engine restarts will lose plans.")
        sys.exit(1)

    # Step 2 — find a test session
    print("\n▶ Step 2: Find a chat_session row to test against")
    session_id = await find_test_session()
    if not session_id:
        print("  ⚠️  No chat_sessions in DB — skipping roundtrip test.")
        print("      (Column probe succeeded, so migration is in place.)")
        sys.exit(0)
    print(f"  ✅ using session_id={session_id[:12]}…")

    # Step 3 — roundtrip
    print("\n▶ Step 3: Save → get → clear roundtrip")
    ok, msg = await roundtrip(session_id)
    print(f"  {'✅' if ok else '❌'} {msg}")

    print()
    print("=" * 70)
    print("Done." if ok else "FAILED")
    print("=" * 70)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
