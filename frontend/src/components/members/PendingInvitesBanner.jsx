"use client";

// ─────────────────────────────────────────────────────────
//  PendingInvitesBanner — surfaces project invitations the
//  signed-in user has been offered but not yet accepted.
//
//  Reads GET /api/invites/me on mount. For each pending invite, shows
//  a card with the project title + an Accept button that calls
//  POST /api/invites/{token}/accept and redirects to the workspace.
//
//  Renders nothing when there are no pending invites — so the dashboard
//  is unchanged for users without invitations. Auto-refreshes after
//  accept/decline so the list stays in sync.
// ─────────────────────────────────────────────────────────

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Mail, Check, X, Loader2 } from "lucide-react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export default function PendingInvitesBanner() {
  const router = useRouter();
  const [invites, setInvites] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyToken, setBusyToken] = useState(null);
  const [error, setError] = useState(null);

  const fetchInvites = useCallback(async () => {
    try {
      const res = await fetch("/api/invites/me");
      if (!res.ok) {
        // 401 means the user isn't signed in or auth dropped — hide the
        // banner silently rather than nag.
        setInvites([]);
        return;
      }
      const data = await res.json();
      setInvites(data?.invites || []);
    } catch {
      setInvites([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + Supabase Realtime subscription. Realtime pushes
  // INSERT/UPDATE rows on project_invites filtered to the current user's
  // email — sub-second notification when an owner sends an invite, and
  // sub-second cleanup when the invite is revoked/expired/accepted from
  // another tab. RLS still applies to the subscription, so the user only
  // gets events for invites they're allowed to read.
  //
  // We don't keep a polling fallback — supabase-js handles socket
  // reconnects with backoff on its own. If the user is offline they
  // wouldn't get a poll either way; they'll see the banner on the next
  // page load (initial fetch on mount).
  useEffect(() => {
    let cancelled = false;
    let channel = null;

    (async () => {
      // 1. Initial fetch — Realtime only pushes new changes.
      await fetchInvites();
      if (cancelled) return;

      // 2. Resolve the current user's email so we can server-side filter
      //    on invitee_email. Subscribing without a filter would still
      //    work (RLS scopes the rows), but a filter is cheaper.
      const supabase = getSupabaseBrowserClient();
      const { data: { user } } = await supabase.auth.getUser();
      if (cancelled || !user?.email) return;
      const filterEmail = user.email.toLowerCase();

      // 3. Open a Realtime channel. The filter matches the lowercased
      //    invitee_email column (which is how we store it).
      channel = supabase
        .channel(`pending-invites:${filterEmail}`)
        .on(
          "postgres_changes",
          {
            event:  "*",  // INSERT (new invite), UPDATE (status flips)
            schema: "public",
            table:  "project_invites",
            filter: `invitee_email=eq.${filterEmail}`,
          },
          () => {
            // Re-fetch rather than mutating local state in place — the
            // server-side query joins chat_sessions for project titles
            // which the Realtime payload doesn't include.
            fetchInvites();
          },
        )
        .subscribe();
    })();

    return () => {
      cancelled = true;
      if (channel) {
        const supabase = getSupabaseBrowserClient();
        supabase.removeChannel(channel);
      }
    };
  }, [fetchInvites]);

  const handleAccept = async (invite) => {
    if (!invite?.token) return;
    setBusyToken(invite.token);
    setError(null);
    try {
      const res = await fetch(
        `/api/invites/${encodeURIComponent(invite.token)}/accept`,
        { method: "POST" },
      );
      const data = await res.json().catch(() => ({}));

      if (res.ok && (data.project_slug || data.project_id)) {
        // The workspace route is keyed by chat_sessions.project_id (text
        // slug) — NOT the UUID PK. The accept endpoint returns both;
        // prefer the slug. Falling back to project_id only saves us when
        // talking to an older backend that didn't return a slug yet.
        const dest = data.project_slug || data.project_id;
        router.push(`/dashboard/workspace/${encodeURIComponent(dest)}`);
      } else if (res.status === 410) {
        setError("This invitation expired or was revoked.");
        await fetchInvites();
      } else if (res.status === 403) {
        setError("This invite was sent to a different email address.");
      } else {
        setError(data.detail || `Couldn't accept (${res.status}).`);
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setBusyToken(null);
    }
  };

  const handleDismiss = (inviteId) => {
    // Client-side dismiss only — we don't have a "decline" endpoint
    // (invites are revoked by owners, not invitees). Hiding the card
    // locally is enough to declutter the dashboard.
    setInvites((prev) => prev.filter((i) => i.invite_id !== inviteId));
  };

  if (loading || invites.length === 0) return null;

  return (
    <div className="max-w-[800px] mx-auto px-8 pt-6">
      <div className="space-y-2">
        {invites.map((inv) => {
          const busy = busyToken === inv.token;
          return (
            <div
              key={inv.invite_id}
              className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-blue-50 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/30">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 grid place-items-center shrink-0">
                  <Mail className="w-4 h-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-slate-900 dark:text-white truncate">
                    You've been invited to{" "}
                    <span className="font-semibold">
                      {inv.project_title || "a project"}
                    </span>
                  </p>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">
                    Accepting will add you as an editor.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => handleAccept(inv)}
                  disabled={busy}
                  className="inline-flex items-center gap-1 h-8 px-3 rounded-md text-[12.5px] font-semibold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60">
                  {busy ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Check className="w-3 h-3" />
                  )}
                  Accept
                </button>
                <button
                  onClick={() => handleDismiss(inv.invite_id)}
                  disabled={busy}
                  className="inline-flex items-center justify-center w-8 h-8 rounded-md text-slate-500 hover:text-slate-700 hover:bg-slate-100 dark:hover:bg-white/[0.06]"
                  title="Hide for now">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          );
        })}
        {error && (
          <div className="px-3 py-2 rounded-md bg-red-50 border border-red-100 text-xs text-red-700">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
