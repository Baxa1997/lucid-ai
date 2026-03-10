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
      // Keep cookies in sync with the latest tokens
      if (session) {
        document.cookie = `sb-access-token=${session.access_token}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`;
        document.cookie = `sb-refresh-token=${session.refresh_token}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`;
      }

      // Only clear cookies on EXPLICIT sign-out
      if (event === 'SIGNED_OUT') {
        document.cookie = 'sb-access-token=; path=/; max-age=0';
        document.cookie = 'sb-refresh-token=; path=/; max-age=0';
      }
    });
  }

  return client;
}
