import { createClient } from '@supabase/supabase-js';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Browser Client
//  Used in 'use client' components for auth actions
// ─────────────────────────────────────────────────────────

let client;

export function getSupabaseBrowserClient() {
  if (client) return client;

  client = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  );

  return client;
}
