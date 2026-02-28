import { createClient } from '@supabase/supabase-js';
import { cookies } from 'next/headers';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Server Client
//  Used in API Routes and Server Actions
// ─────────────────────────────────────────────────────────

/**
 * Creates a Supabase client for server-side use.
 * Reads session tokens from cookies set by the auth callback.
 */
export async function getSupabaseServerClient() {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get('sb-access-token')?.value;
  const refreshToken = cookieStore.get('sb-refresh-token')?.value;

  const supabase = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
      },
    }
  );

  // If we have tokens, set the session manually
  if (accessToken && refreshToken) {
    await supabase.auth.setSession({
      access_token: accessToken,
      refresh_token: refreshToken,
    });
  }

  return supabase;
}

/**
 * Helper: get the access token from cookies directly.
 * Useful when you just need the JWT without creating a full client.
 */
export async function getAccessToken() {
  const cookieStore = await cookies();
  return cookieStore.get('sb-access-token')?.value ?? null;
}
