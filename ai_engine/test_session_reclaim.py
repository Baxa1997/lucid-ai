"""Smoke test: at-cap session creation reclaims the oldest detached session.

Verifies the policy added in sessions.create_session: when a user is at
MAX_SESSIONS_PER_USER and tries to start a new session, the oldest session
whose WebSocket is no longer attached is cancelled+destroyed to free a slot.
If all existing sessions still have an attached WS, the 429 still fires.

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_session_reclaim.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

import app.services.sessions as sessions_mod  # noqa: E402


async def fake_create_runner(*_args, **_kwargs):
    class FakeRunner:
        async def setup(self):
            pass

        async def cleanup(self):
            pass

    return FakeRunner()


async def main() -> None:
    sessions_mod.create_runner = fake_create_runner
    sessions_mod.settings.WORKSPACE_BASE_PATH = "/tmp/test_session_reclaim"
    import os as _os
    _os.makedirs = lambda *a, **kw: None  # type: ignore[assignment]

    from app.services.sessions import (  # noqa: E402
        store,
        create_session,
        AgentSession,
        MAX_SESSIONS_PER_USER,
    )
    from app.services.event_bus import WebSocketProxy  # noqa: E402
    from fastapi import HTTPException  # noqa: E402

    USER = "test-user-reclaim"

    # ── Reset any leftover state from previous runs ───────────
    for s in await store.list_by_user(USER):
        store._sessions.pop(s.session_id, None)

    # ── Case A: at cap, all sessions detached → oldest reclaimed ──
    print("Case A — all 3 detached, expect reclaim")
    pre = []
    for i in range(MAX_SESSIONS_PER_USER):
        sid = f"fake-A-{i}"
        s = AgentSession(session_id=sid, user_id=USER, task=f"old-task-{i}")
        s.pipeline_task = asyncio.ensure_future(asyncio.sleep(3600))
        s.ws_proxy = WebSocketProxy(session_id=sid, websocket=None)  # detached
        s.created_at = sessions_mod.datetime.now(sessions_mod.timezone.utc).replace(
            microsecond=i * 1000,
        )
        await store.add(s)
        pre.append(s)

    new_sess = await create_session(user_id=USER, task="new prompt", )
    survivors = {s.session_id for s in await store.list_by_user(USER)}
    assert "fake-A-0" not in survivors, "oldest detached not reclaimed"
    assert new_sess.session_id in survivors, "new session not added"
    assert len(survivors) == MAX_SESSIONS_PER_USER, f"want {MAX_SESSIONS_PER_USER}, got {len(survivors)}"
    print(f"  ✓ reclaimed fake-A-0; survivors: {sorted(survivors)}")

    # Clean up remaining pretend tasks
    for s in pre[1:]:
        if s.pipeline_task and not s.pipeline_task.done():
            s.pipeline_task.cancel()
    for s in await store.list_by_user(USER):
        store._sessions.pop(s.session_id, None)

    # ── Case B: at cap, all sessions still attached → 429 ──
    print("Case B — all 3 attached, expect 429 (no reclaim)")
    class FakeWS:
        client_state = type("S", (), {"name": "CONNECTED"})()
    pre = []
    for i in range(MAX_SESSIONS_PER_USER):
        sid = f"fake-B-{i}"
        s = AgentSession(session_id=sid, user_id=USER, task=f"live-task-{i}")
        s.pipeline_task = asyncio.ensure_future(asyncio.sleep(3600))
        s.ws_proxy = WebSocketProxy(session_id=sid, websocket=FakeWS())  # attached
        await store.add(s)
        pre.append(s)

    raised_429 = False
    try:
        await create_session(user_id=USER, task="should-fail", )
    except HTTPException as exc:
        if exc.status_code == 429:
            raised_429 = True

    if not raised_429:
        print("  ✗ FAIL — expected 429, got success")
        sys.exit(1)
    survivors = {s.session_id for s in await store.list_by_user(USER)}
    assert survivors == {f"fake-B-{i}" for i in range(MAX_SESSIONS_PER_USER)}, \
        f"existing live sessions disturbed: {survivors}"
    print(f"  ✓ 429 raised; live sessions untouched: {sorted(survivors)}")

    for s in pre:
        if s.pipeline_task and not s.pipeline_task.done():
            s.pipeline_task.cancel()
    for s in await store.list_by_user(USER):
        store._sessions.pop(s.session_id, None)

    print()
    print("PASS — both cases behaved as designed")


if __name__ == "__main__":
    asyncio.run(main())
