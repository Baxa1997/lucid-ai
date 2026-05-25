"use client";

// ─────────────────────────────────────────────────────────
//  useInvitations — pending invitations + realtime updates
//
//  Single hook that surfaces:
//    • invites          — list of pending invites for the signed-in user
//    • count            — `invites.length`, for menu badge
//    • status           — "idle" | "loading" | "ready"
//    • accept(invite)   — POST /api/invites/{token}/accept
//    • dismiss(id)      — local-only dismiss (no decline endpoint yet)
//    • refresh()        — re-fetch from server
//
//  Real-time is delivered via Supabase Realtime on the
//  `project_invites` table — same pattern as PendingInvitesBanner.
//  When a new INSERT arrives (an owner just invited this user):
//    • the list is re-fetched
//    • the registered `onNewInvite(payload)` callbacks are invoked
//      (used by InvitationsListener to show toast + browser notification)
// ─────────────────────────────────────────────────────────

import { useCallback, useEffect, useRef, useState } from "react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { safeJsonFetch } from "@/lib/api/safeFetch";

// Module-level subscribers — InvitationsListener and the menu badge both
// hook into the same Realtime channel, so we share the list and dispatch
// once per new INSERT.
const newInviteSubscribers = new Set();

/** Register a callback fired whenever a new invite INSERT lands. */
export function onNewInviteEvent(fn) {
  newInviteSubscribers.add(fn);
  return () => newInviteSubscribers.delete(fn);
}

function dispatchNewInvite(payload) {
  for (const fn of newInviteSubscribers) {
    try { fn(payload); } catch (_) {}
  }
}

// ── Shared, reference-counted Realtime channel ────────────────
// useInvitations() is mounted by several components at once (sidebar badge,
// invitations page, count banner). supabase.channel() caches channels by
// topic name, so when each instance built its own `invites:<email>` channel,
// the 2nd/3rd call got back the already-subscribed channel and adding a
// `.on('postgres_changes', …)` to it threw:
//   "cannot add postgres_changes callbacks … after subscribe()".
// One shared channel, ref-counted across instances, fans every DB change out
// to each mounted hook via `refetchSubscribers`.
const refetchSubscribers = new Set();
let sharedChannel = null;
let sharedChannelPromise = null;
let sharedRefCount = 0;

function dispatchRefetch() {
  for (const fn of refetchSubscribers) {
    try { fn(); } catch (_) {}
  }
}

function ensureSharedChannel() {
  // Already built, or a build is in flight — the promise guard makes
  // concurrent callers from simultaneous mounts share one creation.
  if (sharedChannel || sharedChannelPromise) return;
  sharedChannelPromise = (async () => {
    try {
      const supabase = getSupabaseBrowserClient();
      const { data: { user } } = await supabase.auth.getUser();
      // Bail if every consumer unmounted while we resolved the user, or
      // there's no signed-in email to filter on.
      if (sharedRefCount <= 0 || !user?.email) return;
      const filterEmail = user.email.toLowerCase();
      sharedChannel = supabase
        .channel(`invites:${filterEmail}`)
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "project_invites",
            filter: `invitee_email=eq.${filterEmail}`,
          },
          () => { dispatchRefetch(); },
        )
        .subscribe();
    } finally {
      sharedChannelPromise = null;
    }
  })();
}

function releaseSharedChannel() {
  if (!sharedChannel) return;
  const supabase = getSupabaseBrowserClient();
  supabase.removeChannel(sharedChannel);
  sharedChannel = null;
}

export function useInvitations() {
  const [invites, setInvites] = useState([]);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const previousIdsRef = useRef(new Set());

  const fetchInvites = useCallback(async () => {
    setStatus((prev) => (prev === "ready" ? "ready" : "loading"));
    try {
      const data = await safeJsonFetch("/api/invites/me");
      const list = data?.invites || [];
      // Detect new arrivals by comparing IDs against the previous snapshot.
      // We do this even on the first fetch (empty `previousIdsRef`) — but
      // only fire the callback on subsequent fetches, so the initial load
      // doesn't spam toasts for invites the user already knew about.
      const newOnes = [];
      const seenBefore = previousIdsRef.current;
      const isFirstLoad = seenBefore.size === 0 && status === "idle";
      for (const inv of list) {
        if (!seenBefore.has(inv.invite_id) && !isFirstLoad) {
          newOnes.push(inv);
        }
      }
      previousIdsRef.current = new Set(list.map((i) => i.invite_id));

      setInvites(list);
      setStatus("ready");

      for (const inv of newOnes) dispatchNewInvite(inv);
    } catch (err) {
      console.warn("Failed to load invites:", err.message);
      setError(err.message);
      setStatus("ready");
    }
  }, [status]);

  // ── Initial load + Realtime subscription ──
  useEffect(() => {
    fetchInvites();

    // Join the shared channel: register this instance's refetch and bump the
    // ref count. The last instance to unmount tears the channel down.
    const refetch = () => { fetchInvites(); };
    refetchSubscribers.add(refetch);
    sharedRefCount += 1;
    ensureSharedChannel();

    return () => {
      refetchSubscribers.delete(refetch);
      sharedRefCount -= 1;
      if (sharedRefCount <= 0) {
        sharedRefCount = 0;
        releaseSharedChannel();
      }
    };
    // fetchInvites is wrapped with useCallback but its identity changes on
    // each status flip — running this effect only on mount is correct here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const accept = useCallback(async (invite) => {
    if (!invite?.token) return { ok: false, reason: "no_token" };
    try {
      const data = await safeJsonFetch(
        `/api/invites/${encodeURIComponent(invite.token)}/accept`,
        { method: "POST" },
      );
      await fetchInvites();
      return {
        ok: true,
        projectId: data?.project_id || null,
        projectSlug: data?.project_slug || null,
        projectTitle: data?.project_title || null,
      };
    } catch (err) {
      return { ok: false, reason: err.message };
    }
  }, [fetchInvites]);

  const dismiss = useCallback((inviteId) => {
    // No backend decline endpoint exists — soft local dismiss only.
    // Matches the existing PendingInvitesBanner behaviour.
    setInvites((prev) => prev.filter((i) => i.invite_id !== inviteId));
  }, []);

  return {
    invites,
    count: invites.length,
    status,
    error,
    refresh: fetchInvites,
    accept,
    dismiss,
  };
}
