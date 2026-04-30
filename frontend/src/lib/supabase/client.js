import { createClient } from '@supabase/supabase-js';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Browser Client
//  Used in 'use client' components for auth actions.
//
//  IMPORTANT: A global onAuthStateChange listener keeps
//  the sb-access-token and sb-refresh-token cookies in sync
//  with the Supabase session stored in localStorage.
//  Without this, the middleware (which reads cookies) can
//  see stale/expired tokens and redirect to /login when
//  the user switches tabs and the SDK refreshes silently.
// ─────────────────────────────────────────────────────────

let client;
let listenerAttached = false;

/**
 * Clear ALL Supabase-related cookies.
 * Handles both our custom cookies and any default Supabase cookies
 * matching the sb-*-auth-token pattern.
 */
export function clearAllSupabaseCookies() {
  if (typeof document === 'undefined') return;
  // Clear our explicit cookies
  document.cookie = 'sb-access-token=; path=/; max-age=0';
  document.cookie = 'sb-refresh-token=; path=/; max-age=0';

  // Clear any default Supabase auth cookies (sb-<project-ref>-auth-token*)
  const allCookies = document.cookie.split(';');
  for (const cookie of allCookies) {
    const name = cookie.split('=')[0].trim();
    if (name.startsWith('sb-') && name.includes('-auth-token')) {
      document.cookie = `${name}=; path=/; max-age=0`;
    }
  }
}

/**
 * Sync cookies with the current Supabase session tokens.
 */
export function syncCookiesFromSession(session) {
  if (typeof document === 'undefined') return;
  if (!session?.access_token) return;
  // Secure flag on HTTPS — Safari/iOS strip non-secure cookies set right
  // after a cross-site OAuth redirect, which manifests as "login doesn't
  // navigate to dashboard": cookie set, redirect fires, middleware sees no
  // cookie, bounces back to /login.
  const isHttps = window.location.protocol === 'https:';
  const secure = isHttps ? '; secure' : '';
  document.cookie = `sb-access-token=${session.access_token}; path=/; max-age=${60 * 60 * 24}; samesite=lax${secure}`;
  document.cookie = `sb-refresh-token=${session.refresh_token}; path=/; max-age=${60 * 60 * 24}; samesite=lax${secure}`;
}

export function getSupabaseBrowserClient() {
  if (client) return client;

  client = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  );

  // Attach a global auth state listener ONCE
  if (!listenerAttached) {
    listenerAttached = true;

    client.auth.onAuthStateChange((event, session) => {
      // Keep cookies in sync for ALL session-bearing events
      if (session && (
        event === 'SIGNED_IN' ||
        event === 'TOKEN_REFRESHED' ||
        event === 'INITIAL_SESSION' ||
        event === 'USER_UPDATED'
      )) {
        syncCookiesFromSession(session);
      }

      // Clear ALL cookies on explicit sign-out
      if (event === 'SIGNED_OUT') {
        clearAllSupabaseCookies();
      }
    });
  }

  return client;
}
