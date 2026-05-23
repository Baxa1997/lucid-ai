"use client";

// ─────────────────────────────────────────────────────────
//  InvitationsListener — global side-effect component.
//  Mounts once at the dashboard layout. Listens for new invite
//  events (dispatched by useInvitations Realtime hook) and:
//    • shows an in-app toast linking to /dashboard/invitations
//    • fires a browser Notification when permission has been granted
//    • prompts for browser notification permission once on first mount
//
//  Renders an inline toast (not the generic Toast component, which has
//  a fixed 3s timer and no action button — we need 8s + a "View" button).
// ─────────────────────────────────────────────────────────

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, X, Mail } from "lucide-react";
import { onNewInviteEvent } from "@/hooks/useInvitations";

const PERMISSION_DISMISSED_KEY = "lucid-notif-permission-dismissed-at";
const PERMISSION_REPROMPT_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
const TOAST_AUTODISMISS_MS = 8000;

function notificationSupported() {
  return typeof window !== "undefined" && "Notification" in window;
}

function shouldOfferPermission() {
  if (!notificationSupported()) return false;
  if (Notification.permission !== "default") return false;
  try {
    const lastDismissed = Number(
      localStorage.getItem(PERMISSION_DISMISSED_KEY) || 0,
    );
    return !lastDismissed || Date.now() - lastDismissed > PERMISSION_REPROMPT_MS;
  } catch {
    return true;
  }
}

export default function InvitationsListener() {
  const router = useRouter();
  const [toast, setToast] = useState(null); // { id, projectName }
  const [offerBanner, setOfferBanner] = useState(false);
  const toastTimerRef = useRef(null);

  // Offer permission banner once per session if appropriate.
  useEffect(() => {
    setOfferBanner(shouldOfferPermission());
  }, []);

  // Subscribe to Realtime invite events.
  useEffect(() => {
    const unsubscribe = onNewInviteEvent((invite) => {
      const projectName = invite.project_title || "a new project";

      setToast({
        id: invite.invite_id,
        projectName,
      });
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
      toastTimerRef.current = setTimeout(() => {
        setToast(null);
      }, TOAST_AUTODISMISS_MS);

      if (
        notificationSupported() &&
        Notification.permission === "granted" &&
        typeof document !== "undefined" &&
        document.visibilityState !== "visible"
      ) {
        try {
          const n = new Notification("New project invitation", {
            body: `You've been invited to ${projectName}`,
            tag: `invite-${invite.invite_id}`,
            icon: "/favicon.ico",
          });
          n.onclick = () => {
            window.focus();
            router.push("/dashboard/invitations");
            n.close();
          };
        } catch (_) {}
      }
    });
    return () => {
      unsubscribe();
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, [router]);

  const handleEnable = async () => {
    if (!notificationSupported()) return;
    try {
      await Notification.requestPermission();
    } catch (_) {}
    setOfferBanner(false);
  };

  const handleDismissBanner = () => {
    try {
      localStorage.setItem(PERMISSION_DISMISSED_KEY, String(Date.now()));
    } catch (_) {}
    setOfferBanner(false);
  };

  const handleToastView = () => {
    setToast(null);
    router.push("/dashboard/invitations");
  };

  const handleToastClose = () => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast(null);
  };

  return (
    <>
      {toast && (
        <div className="fixed top-5 right-5 z-[9999] animate-in slide-in-from-top-2 fade-in duration-200">
          <div className="flex items-start gap-3 px-4 py-3 bg-white dark:bg-[#161b22] border border-blue-200 dark:border-blue-500/30 rounded-xl shadow-lg shadow-black/5 dark:shadow-black/30 min-w-[280px] max-w-[400px]">
            <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-300 grid place-items-center shrink-0 mt-0.5">
              <Mail className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                New project invitation
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 truncate">
                You've been invited to {toast.projectName}
              </p>
              <button
                onClick={handleToastView}
                className="mt-2 px-3 py-1 rounded-md bg-blue-600 text-white text-xs font-semibold hover:bg-blue-700">
                View
              </button>
            </div>
            <button
              onClick={handleToastClose}
              className="p-0.5 rounded text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 shrink-0"
              aria-label="Dismiss">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {offerBanner && (
        <div className="fixed top-4 right-4 z-[120] max-w-sm bg-white dark:bg-[#161b22] rounded-xl border border-slate-200 dark:border-[#2d333b] shadow-2xl shadow-slate-900/10 p-4 animate-in slide-in-from-top-2 fade-in duration-200">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-lg bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] flex items-center justify-center shrink-0">
              <Bell className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                Get notified about new project invitations?
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                We'll alert you when someone invites you to collaborate.
              </p>
              <div className="flex items-center gap-2 mt-3">
                <button
                  onClick={handleEnable}
                  className="px-3 py-1.5 rounded-md bg-[#dc5426] text-white text-xs font-semibold hover:bg-[#b8421e]">
                  Enable notifications
                </button>
                <button
                  onClick={handleDismissBanner}
                  className="px-3 py-1.5 rounded-md text-xs font-medium text-slate-500 hover:bg-slate-100 dark:hover:bg-white/[0.05]">
                  Not now
                </button>
              </div>
            </div>
            <button
              onClick={handleDismissBanner}
              className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              aria-label="Dismiss">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
