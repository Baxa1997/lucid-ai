"use client";

// ─────────────────────────────────────────────────────────
//  /accept-invite — landing page for invitation magic links.
//  Reads ?token=..., checks auth, calls the accept endpoint, redirects
//  to the project workspace. Unauthenticated users are bounced to the
//  login page with a return URL that brings them back here.
//
//  Suspense boundary is required because `useSearchParams()` in
//  Next.js 14 forces a CSR bailout during static prerendering; without
//  wrapping the consumer in <Suspense>, `next build` errors with
//  "useSearchParams() should be wrapped in a suspense boundary".
// ─────────────────────────────────────────────────────────

import { Suspense, useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, Check } from "lucide-react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

function AcceptInviteInner() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");

  const [status, setStatus] = useState("loading"); // loading | error | redirecting
  const [errorMsg, setErrorMsg] = useState("");

  const acceptInvite = useCallback(
    async (currentToken) => {
      try {
        const res = await fetch(
          `/api/invites/${encodeURIComponent(currentToken)}/accept`,
          { method: "POST" },
        );
        const data = await res.json().catch(() => ({}));

        if (res.ok && (data.project_slug || data.project_id)) {
          setStatus("redirecting");
          // The workspace route is keyed by chat_sessions.project_id (a TEXT
          // slug). The accept endpoint returns it as project_slug. We fall
          // back to project_id only for older API responses that didn't
          // surface the slug — old links would have linked to a 404 anyway.
          const dest = data.project_slug || data.project_id;
          router.replace(`/dashboard/workspace/${encodeURIComponent(dest)}`);
          return;
        }

        let message;
        if (res.status === 404) {
          message = "This invite link is invalid.";
        } else if (res.status === 410) {
          message =
            "This invite has expired or been revoked. Ask the project owner to send a new one.";
        } else if (res.status === 403) {
          message =
            "This invite was sent to a different email address. Sign in with the invited email to accept.";
        } else if (res.status === 409) {
          message = "This invite has already been accepted.";
        } else {
          message = data.detail || "Failed to accept invite — please try again.";
        }
        setErrorMsg(message);
        setStatus("error");
      } catch (err) {
        console.error("Accept invite failed:", err);
        setErrorMsg("Network error — please try again.");
        setStatus("error");
      }
    },
    [router],
  );

  useEffect(() => {
    if (!token) {
      setErrorMsg("Missing invite token in URL.");
      setStatus("error");
      return;
    }

    let cancelled = false;
    (async () => {
      const supabase = getSupabaseBrowserClient();
      const { data: { user } } = await supabase.auth.getUser();

      if (cancelled) return;

      if (!user) {
        // Send the user to login, returning here after sign-in.
        const returnTo = encodeURIComponent(`/accept-invite?token=${token}`);
        router.replace(`/login?returnTo=${returnTo}`);
        return;
      }

      await acceptInvite(token);
    })();

    return () => {
      cancelled = true;
    };
  }, [token, router, acceptInvite]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6">
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 w-full max-w-md p-8 text-center">
        {status === "loading" || status === "redirecting" ? (
          <>
            <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-blue-100 grid place-items-center">
              <Loader2 className="w-6 h-6 text-blue-600 animate-spin" />
            </div>
            <h1 className="text-base font-semibold text-slate-900 mb-1">
              {status === "loading" ? "Accepting invite…" : "Opening project…"}
            </h1>
            <p className="text-sm text-slate-500">
              {status === "loading"
                ? "Verifying your invitation."
                : "Taking you to the project workspace."}
            </p>
          </>
        ) : (
          <>
            <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-red-100 grid place-items-center">
              <AlertCircle className="w-6 h-6 text-red-600" />
            </div>
            <h1 className="text-base font-semibold text-slate-900 mb-1">
              Can't accept this invite
            </h1>
            <p className="text-sm text-slate-500 mb-5">{errorMsg}</p>
            <button
              onClick={() => router.push("/dashboard")}
              className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md">
              Go to dashboard
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function AcceptInviteFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6">
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 w-full max-w-md p-8 text-center">
        <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-blue-100 grid place-items-center">
          <Loader2 className="w-6 h-6 text-blue-600 animate-spin" />
        </div>
        <h1 className="text-base font-semibold text-slate-900 mb-1">
          Loading invite…
        </h1>
      </div>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<AcceptInviteFallback />}>
      <AcceptInviteInner />
    </Suspense>
  );
}
