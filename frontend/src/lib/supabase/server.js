import { createClient } from '@supabase/supabase-js';
import { cookies } from 'next/headers';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Server Client
//  Used in API Routes and Server Actions
//
//  Uses the access token from cookies as an Authorization
//  header — no network call needed. This avoids the
//  intermittent `fetch failed` errors that occur when
//  setSession() tries to refresh an expired token in Docker.
// ─────────────────────────────────────────────────────────

/**
 * Creates a Supabase client for server-side use.
 * Reads the access token from cookies and passes it via
 * the Authorization header — RLS policies evaluate it via
 * auth.uid() without any network round-trip to Supabase Auth.
 */
export async function getSupabaseServerClient() {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get('sb-access-token')?.value;

  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
      },
      global: accessToken
        ? { headers: { Authorization: `Bearer ${accessToken}` } }
        : {},
    }
  );
}

/**
 * Helper: get the access token from cookies directly.
 * Useful when you just need the JWT without creating a full client.
 */
export async function getAccessToken() {
  const cookieStore = await cookies();
  return cookieStore.get('sb-access-token')?.value ?? null;
}
