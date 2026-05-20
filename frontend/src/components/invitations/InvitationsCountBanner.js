"use client";

// ─────────────────────────────────────────────────────────
//  InvitationsCountBanner — condensed pending-invites prompt
//  rendered at the top of the Home page.
//
//  Renders nothing when there are no pending invitations.
//  One invite  → "You've been invited to {project} — View invitation"
//  N invites   → "You have N pending invitations — View invitations"
//
//  Clicking anywhere on the banner routes to /dashboard/engineer/invitations.
//  Powered by the same useInvitations hook used by the sidebar badge, so
//  it picks up new invites via Supabase Realtime without extra wiring.
// ─────────────────────────────────────────────────────────

import Link from "next/link";
import { Mail, ArrowRight } from "lucide-react";
import { useInvitations } from "@/hooks/useInvitations";

export default function InvitationsCountBanner() {
  const { invites, count, status } = useInvitations();

  if (status !== "ready" || count === 0) return null;

  const single = count === 1;
  const projectTitle = single ? (invites[0]?.project_title || "a new project") : null;

  const message = single
    ? <>You've been invited to <span className="font-semibold">{projectTitle}</span></>
    : <>You have <span className="font-semibold">{count}</span> pending invitations</>;

  const cta = single ? "View invitation" : "View invitations";

  return (
    <div className="max-w-[800px] mx-auto px-8 pt-6">
      <Link
        href="/dashboard/engineer/invitations"
        className="group flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-blue-50 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/30 hover:bg-blue-100/60 dark:hover:bg-blue-500/15 transition-colors">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-full bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 grid place-items-center shrink-0 relative">
            <Mail className="w-4 h-4" />
            {!single && (
              <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
                {count > 9 ? "9+" : count}
              </span>
            )}
          </div>
          <p className="text-[13.5px] text-slate-900 dark:text-white truncate">
            {message}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 text-[13px] font-semibold text-blue-700 dark:text-blue-300 shrink-0">
          {cta}
          <ArrowRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      </Link>
    </div>
  );
}
