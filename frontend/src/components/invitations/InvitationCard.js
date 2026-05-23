"use client";

// ─────────────────────────────────────────────────────────
//  InvitationCard — single pending-invite row on the
//  /dashboard/invitations page.
//
//  Accept calls POST /api/invites/{token}/accept (delegated to the
//  parent's `onAccept`), which on success routes the user into the
//  workspace.
//
//  Decline is a soft-dismiss only — the existing backend has no
//  /decline endpoint, so this just removes the row locally. Matches
//  the existing PendingInvitesBanner behaviour.
// ─────────────────────────────────────────────────────────

import { useState } from "react";
import { Mail, Check, X, Loader2, Calendar } from "lucide-react";

function relativeDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function InvitationCard({
  invite,
  onAccept,
  onDecline,
  busy,
  error,
}) {
  const [confirming, setConfirming] = useState(false);

  const projectTitle = invite.project_title || "Untitled project";

  const handleDeclineClick = () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    onDecline?.(invite);
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#161b22] p-5 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4 min-w-0">
          <div className="w-11 h-11 rounded-xl bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-300 grid place-items-center shrink-0">
            <Mail className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <p className="text-base font-semibold text-slate-900 dark:text-white truncate">
              {projectTitle}
            </p>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              You've been invited to collaborate as an editor.
            </p>
            <div className="flex items-center gap-3 mt-2 text-xs text-slate-400 dark:text-slate-500">
              <span className="inline-flex items-center gap-1">
                <Calendar className="w-3 h-3" />
                Sent {relativeDate(invite.created_at)}
              </span>
              {invite.expires_at && (
                <span>Expires {relativeDate(invite.expires_at)}</span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {confirming ? (
            <>
              <span className="text-xs text-slate-500 dark:text-slate-400 mr-1">
                Decline?
              </span>
              <button
                onClick={() => setConfirming(false)}
                disabled={busy}
                className="px-3 py-1.5 rounded-md text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06]">
                Cancel
              </button>
              <button
                onClick={handleDeclineClick}
                disabled={busy}
                className="px-3 py-1.5 rounded-md text-xs font-semibold text-white bg-red-600 hover:bg-red-700 disabled:opacity-60">
                Yes, decline
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleDeclineClick}
                disabled={busy}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] disabled:opacity-60"
                title="Decline">
                <X className="w-3.5 h-3.5" />
                Decline
              </button>
              <button
                onClick={() => onAccept?.(invite)}
                disabled={busy}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-md text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60">
                {busy ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
                Accept
              </button>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="mt-3 px-3 py-2 rounded-md bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/30 text-xs text-red-700 dark:text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
