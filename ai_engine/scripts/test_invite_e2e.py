"""Live end-to-end test for the invite flow.

Requires:
  • SUPABASE_URL / SUPABASE_SERVICE_KEY pointing at a test project
  • two pre-created test users in auth.users (alice + bob)
  • their UUIDs and emails passed via env vars

Env vars
--------
  ALICE_USER_ID       UUID of the owner user (must already exist in public.users)
  ALICE_EMAIL         email of the owner user
  BOB_USER_ID         UUID of the invitee user
  BOB_EMAIL           email of the invitee user
  TEST_PROJECT_TITLE  optional — defaults to "E2E invite test"

What it does
------------
1. Service-role creates a chat_sessions row owned by Alice. The
   chat_sessions_add_owner trigger inserts a project_members row.
2. Calls InvitationService.create_invite (alice → bob_email).
3. Verifies a project_invites row was inserted with status='pending'.
4. Calls accept_invite as Bob with his email — verifies success.
5. Reads project_members; expects Bob present with role='editor'.
6. Lists projects-for-user(bob) — expects this session present.
7. Calls remove_member(bob) — verifies success.
8. Reads project_members again; expects Bob absent.
9. Cleans up: deletes the chat_sessions row (cascades invites + members).

This script does NOT verify the Supabase email-send step end-to-end —
inspect the Supabase dashboard's email logs to confirm the magic link was
sent. Email delivery failure is non-fatal in the production path
(returns 200 + warning), and this script focuses on DB-state correctness.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"  MISSING env var: {name}")
        sys.exit(2)
    return v


def _step(n: int, msg: str) -> None:
    print(f"\n── step {n}: {msg}")


async def main() -> int:
    alice_id    = _require("ALICE_USER_ID")
    alice_email = _require("ALICE_EMAIL")
    bob_id      = _require("BOB_USER_ID")
    bob_email   = _require("BOB_EMAIL")
    project_title = os.environ.get("TEST_PROJECT_TITLE", "E2E invite test")

    from app.services.members import InvitationService, MembershipService
    from app.supabase_client import managed_admin_client

    failures: list[str] = []
    project_id: str | None = None

    try:
        # ── 1. Create chat_sessions row (Alice as owner via trigger) ────
        _step(1, "creating chat_sessions row (alice as owner)")
        async with managed_admin_client() as client:
            ins = (
                await client.table("chat_sessions")
                .insert({"user_id": alice_id, "title": project_title})
                .execute()
            )
            project_id = ins.data[0]["id"]
        print(f"   project_id = {project_id}")

        # ── 2. Alice invites Bob by email ───────────────────────────────
        _step(2, f"alice invites {bob_email}")
        invite = await InvitationService.create_invite(
            project_id=project_id,
            inviter_id=alice_id,
            invitee_email=bob_email,
        )
        token = invite["token"]
        invite_id = invite["id"]
        print(f"   invite_id = {invite_id}  token={token[:8]}…")

        # ── 3. Verify pending invite row exists ─────────────────────────
        _step(3, "verifying pending invite row")
        async with managed_admin_client() as client:
            r = (
                await client.table("project_invites")
                .select("status, invitee_email")
                .eq("id", invite_id)
                .maybe_single()
                .execute()
            )
            row = r.data or {}
            if row.get("status") != "pending":
                failures.append(f"expected status=pending, got {row.get('status')!r}")
            if (row.get("invitee_email") or "").lower() != bob_email.lower():
                failures.append("invitee_email mismatch on inserted row")
        print(f"   row status={row.get('status')}, email={row.get('invitee_email')}")

        # ── 4. Bob accepts ──────────────────────────────────────────────
        _step(4, "bob accepts the invite")
        accepted_pid = await InvitationService.accept_invite(
            token=token,
            accepting_user_id=bob_id,
            accepting_email=bob_email,
        )
        if accepted_pid != project_id:
            failures.append(f"accept returned {accepted_pid!r}, expected {project_id!r}")
        print(f"   → returned project_id = {accepted_pid}")

        # ── 5. Confirm Bob is now in project_members ───────────────────
        _step(5, "verifying bob is in project_members")
        members = await MembershipService.list_members(project_id, user_jwt=None)
        roles = {m["user_id"]: m["role"] for m in members}
        if roles.get(bob_id) != "editor":
            failures.append(f"expected bob role=editor, got {roles.get(bob_id)!r}")
        print(f"   members: {roles}")

        # ── 6. Bob's project list includes the session ─────────────────
        _step(6, "verifying bob can see the project in list_projects_for_user")
        bob_projects = await MembershipService.list_projects_for_user(bob_id, user_jwt=None)
        if not any(p["project_id"] == project_id for p in bob_projects):
            failures.append("project not present in bob's project list")
        print(f"   bob sees {len(bob_projects)} project(s)")

        # ── 7. Alice removes Bob ───────────────────────────────────────
        _step(7, "alice removes bob from the project")
        await MembershipService.remove_member(project_id=project_id, user_id=bob_id)

        # ── 8. Confirm Bob is no longer a member ───────────────────────
        _step(8, "verifying bob no longer has access")
        members_after = await MembershipService.list_members(project_id, user_jwt=None)
        if any(m["user_id"] == bob_id for m in members_after):
            failures.append("bob still present after remove")
        print(f"   members after removal: {[m['user_id'] for m in members_after]}")

    finally:
        # ── 9. Cleanup ──────────────────────────────────────────────────
        if project_id:
            _step(9, "cleanup — deleting chat_sessions row (cascades)")
            try:
                async with managed_admin_client() as client:
                    await (
                        client.table("chat_sessions")
                        .delete()
                        .eq("id", project_id)
                        .execute()
                    )
            except Exception as exc:
                print(f"   cleanup error (ignored): {exc}")

    if failures:
        print("\n── FAILURES ──")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print("\n── PASSED ── all invite e2e assertions held")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
