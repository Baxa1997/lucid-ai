"use client";

// ─────────────────────────────────────────────────────────
//  MembersList — current members + pending invites for a project.
//  Used inside InviteDialog. Read-only for non-owners; owner sees
//  revoke (pending invite) and remove (existing member) buttons.
// ─────────────────────────────────────────────────────────

import { useState } from "react";
import { Loader2, Trash2, Mail, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";

function relativeDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function MembersList({
  members,
  invites,
  currentUserId,
  isOwner,
  onRevoke,
  onRemove,
}) {
  const [busyId, setBusyId] = useState(null);

  const handleRevoke = async (inviteId) => {
    setBusyId(`invite:${inviteId}`);
    try { await onRevoke(inviteId); } finally { setBusyId(null); }
  };

  const handleRemove = async (userId) => {
    setBusyId(`member:${userId}`);
    try { await onRemove(userId); } finally { setBusyId(null); }
  };

  return (
    <div className="space-y-4">
      {/* ── Current members ── */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
          Members ({members?.length || 0})
        </h3>
        <div className="space-y-1.5">
          {(members || []).map((m) => {
            const label = m.name || m.email || m.user_id;
            const isSelf = m.user_id === currentUserId;
            const canRemove = isOwner && !isSelf && m.role !== "owner";
            const busy = busyId === `member:${m.user_id}`;
            return (
              <div
                key={m.user_id}
                className="flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-slate-200 bg-white">
                <div className="flex items-center gap-3 min-w-0">
                  {m.avatar_url ? (
                    <img
                      src={m.avatar_url}
                      alt=""
                      className="w-7 h-7 rounded-full object-cover bg-slate-200"
                    />
                  ) : (
                    <div className="w-7 h-7 rounded-full bg-slate-100 text-slate-500 grid place-items-center text-xs font-medium">
                      {(label || "?").charAt(0).toUpperCase()}
                    </div>
                  )}
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-900 truncate">
                      {label}
                      {isSelf && (
                        <span className="ml-1.5 text-xs text-slate-400">(you)</span>
                      )}
                    </div>
                    {m.email && m.name && (
                      <div className="text-xs text-slate-500 truncate">{m.email}</div>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span
                    className={cn(
                      "text-[11px] font-medium px-2 py-0.5 rounded",
                      m.role === "owner"
                        ? "bg-blue-50 text-blue-700"
                        : "bg-slate-100 text-slate-600",
                    )}>
                    {m.role === "owner" ? (
                      <span className="inline-flex items-center gap-1">
                        <ShieldCheck className="w-3 h-3" />
                        Owner
                      </span>
                    ) : (
                      "Editor"
                    )}
                  </span>
                  {canRemove && (
                    <button
                      onClick={() => handleRemove(m.user_id)}
                      disabled={busy}
                      className="p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-50"
                      title="Remove member">
                      {busy ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="w-3.5 h-3.5" />
                      )}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          {(!members || members.length === 0) && (
            <div className="text-sm text-slate-400 px-3 py-2">No members yet.</div>
          )}
        </div>
      </div>

      {/* ── Pending invites (only when there are any) ── */}
      {invites && invites.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
            Pending invitations ({invites.length})
          </h3>
          <div className="space-y-1.5">
            {invites.map((i) => {
              const busy = busyId === `invite:${i.invite_id}`;
              return (
                <div
                  key={i.invite_id}
                  className="flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-amber-200 bg-amber-50/50">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-7 h-7 rounded-full bg-amber-100 text-amber-700 grid place-items-center">
                      <Mail className="w-3.5 h-3.5" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-slate-900 truncate">
                        {i.email}
                      </div>
                      <div className="text-xs text-slate-500">
                        Expires {relativeDate(i.expires_at)}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[11px] font-medium px-2 py-0.5 rounded bg-amber-100 text-amber-700">
                      Pending
                    </span>
                    {isOwner && (
                      <button
                        onClick={() => handleRevoke(i.invite_id)}
                        disabled={busy}
                        className="p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-50"
                        title="Revoke invite">
                        {busy ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="w-3.5 h-3.5" />
                        )}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
