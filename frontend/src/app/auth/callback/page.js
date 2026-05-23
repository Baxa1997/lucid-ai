'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Auth Callback (Client-Side)
//  Handles the OAuth code exchange for login (Google, etc.)
//
//  Supports both:
//  1. PKCE flow — code is in query params (?code=xxx)
//  2. Implicit flow — tokens are in URL hash (#access_token=...)
// ─────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getSupabaseBrowserClient, syncCookiesFromSession } from '@/lib/supabase/client';

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState(null);

  useEffect(() => {
    // Check for error in query params first
    const params = new URLSearchParams(window.location.search);
    const errorParam = params.get('error_description') || params.get('error');
    if (errorParam) {
      setError(errorParam.replace(/\+/g, ' '));
      return;
    }

    const supabase = getSupabaseBrowserClient();
    let redirected = false;

    const doRedirect = (sessionOverride) => {
      if (redirected) return;
      redirected = true;
      
      // Explicitly sync cookies right before redirecting to prevent race conditions 
      // where the global listener hasn't written the cookie yet
      if (sessionOverride) {
        syncCookiesFromSession(sessionOverride);
      }

      sessionStorage.setItem('lucid-just-signed-in', 'true');
      router.replace('/dashboard');
    };

    // ── PKCE Code Exchange ──
    // If Supabase sent a `code` query param, exchange it for a session.
    const code = params.get('code');
    if (code) {
      supabase.auth.exchangeCodeForSession(code)
        .then(({ data, error: exchangeError }) => {
          if (exchangeError) {
            console.error('[AuthCallback] Code exchange failed:', exchangeError);
            setError(exchangeError.message);
            return;
          }
          if (data?.session) {
            doRedirect(data.session);
          }
        })
        .catch((err) => {
          console.error('[AuthCallback] Unexpected error during code exchange:', err);
          setError('Authentication failed. Please try again.');
        });
    }

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if ((event === 'SIGNED_IN' || event === 'INITIAL_SESSION') && session) {
        doRedirect(session);
      }
    });

    // Also check if session already exists (e.g. page reload during callback)
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        doRedirect(session);
      }
    });

    // Timeout — give more time for slow networks
    const timeout = setTimeout(() => {
      if (!redirected) {
        setError('Authentication timed out. Please try again.');
      }
    }, 20000);

    return () => {
      clearTimeout(timeout);
      subscription?.unsubscribe();
    };
  }, [router]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white dark:bg-[#0d1117]">
        <div className="text-center max-w-md px-6">
          <div className="w-12 h-12 rounded-full bg-red-50 dark:bg-red-500/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <p className="text-red-500 text-sm font-medium mb-2">Authentication Failed</p>
          <p className="text-slate-400 dark:text-slate-500 text-xs mb-6">{error}</p>
          <a href="/login" className="inline-flex items-center gap-2 text-blue-600 text-sm font-medium hover:underline">
            ← Back to login
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white dark:bg-[#0d1117]">
      <div className="text-center">
        <div className="w-8 h-8 border-3 border-blue-200 border-t-blue-600 rounded-full animate-spin mx-auto mb-4" />
        <p className="text-slate-500 dark:text-slate-400 text-sm font-medium">
          Completing sign in...
        </p>
      </div>
    </div>
  );
}
