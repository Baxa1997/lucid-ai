"use client";

// ─────────────────────────────────────────────────────────
//  /dashboard/invitations
//  Pending-invitations page.
//
//  Lists invites for the signed-in user (project name, sender info
//  if available, invited date, role). Accept routes into the
//  workspace; decline soft-dismisses (no backend decline endpoint
//  yet — see useInvitations.dismiss).
// ─────────────────────────────────────────────────────────

import { useState } from "react";
import { Loader2, Inbox } from "lucide-react";
import { useInvitations } from "@/hooks/useInvitations";
import InvitationCard from "@/components/invitations/InvitationCard";

export default function InvitationsPage() {
  const { invites, status, accept, dismiss } = useInvitations();
  const [busyToken, setBusyToken] = useState(null);
  const [rowError, setRowError] = useState({});
  const [success, setSuccess] = useState(null);

  const handleAccept = async (invite) => {
    setRowError((prev) => ({ ...prev, [invite.invite_id]: null }));
    setBusyToken(invite.token);
    try {
      const result = await accept(invite);
      if (result.ok) {
        // Don't navigate — just confirm. The project will show up in
        // "All apps" automatically (membership row was just inserted).
        setSuccess(
          `Joined ${result.projectTitle || "the project"} — find it under All apps.`,
        );
      } else {
        setRowError((prev) => ({
          ...prev,
          [invite.invite_id]: result.reason || "Could not accept",
        }));
      }
    } finally {
      setBusyToken(null);
    }
  };

  const handleDecline = (invite) => {
    dismiss(invite.invite_id);
  };

  const isEmpty = status === "ready" && invites.length === 0;

  return (
    <div className="flex flex-col min-h-full bg-[#fefcfa] dark:bg-[#0d1117]">
      {/* Header — full width */}
      <div className="px-8 lg:px-12 pt-10 pb-6 border-b border-slate-200/60 dark:border-[#2d333b]">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
              Invitations
            </h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
              Projects you've been invited to collaborate on.
            </p>
          </div>
          {invites.length > 0 && (
            <span className="px-2.5 py-1 rounded-full bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300 text-xs font-semibold">
              {invites.length} pending
            </span>
          )}
        </div>
      </div>

      {/* Success banner */}
      {success && (
        <div className="px-8 lg:px-12 pt-5">
          <div className="px-4 py-3 rounded-xl bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30 text-sm text-emerald-700 dark:text-emerald-300">
            {success}
          </div>
        </div>
      )}

      {/* Body — fills remaining space, empty state stays vertically centered */}
      <div className={isEmpty ? "flex-1 flex items-center justify-center px-8 lg:px-12 py-10" : "px-8 lg:px-12 py-8"}>
        {status === "loading" || status === "idle" ? (
          <div className="flex-1 flex items-center justify-center py-20 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : isEmpty ? (
          <EmptyState />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 w-full">
            {invites.map((inv) => (
              <InvitationCard
                key={inv.invite_id}
                invite={inv}
                onAccept={handleAccept}
                onDecline={handleDecline}
                busy={busyToken === inv.token}
                error={rowError[inv.invite_id]}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="w-full max-w-xl rounded-2xl border border-dashed border-slate-200 dark:border-[#2d333b] py-20 px-8 text-center">
      <div className="w-16 h-16 mx-auto rounded-2xl bg-slate-100 dark:bg-white/[0.04] text-slate-400 grid place-items-center mb-5">
        <Inbox className="w-8 h-8" />
      </div>
      <h2 className="text-lg font-semibold text-slate-700 dark:text-slate-200">
        No pending invitations
      </h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 max-w-md mx-auto">
        Project owners can invite you to collaborate. Invitations will appear
        here as soon as they arrive.
      </p>
    </div>
  );
}
