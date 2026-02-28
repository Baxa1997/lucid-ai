// ─────────────────────────────────────────────────────────
//  Lucid AI — Gatekeeper Utilities
//  Shared auth-check + forwarding helpers for API routes
//
//  Uses Supabase Auth (replaces NextAuth).
//  Forwards the user's Supabase JWT as Authorization: Bearer
//  to the Python ai_engine, which validates it natively.
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import { getSupabaseServerClient, getAccessToken } from '@/lib/supabase/server';

export const AI_SERVICE_URL =
  process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';

const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';

// ═══════════════════════════════════════════════════════════
//  Auth Guard
// ═══════════════════════════════════════════════════════════

/**
 * Validate the current Supabase session and extract userId + JWT.
 *
 * Returns either:
 *   { ok: true,  ctx: { userId, accessToken, user } }
 *   { ok: false, response: NextResponse }
 */
export async function requireAuth() {
  const supabase = await getSupabaseServerClient();
  const accessToken = await getAccessToken();

  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  if (error || !user) {
    return {
      ok: false,
      response: NextResponse.json(
        { error: 'Unauthorized', message: 'You must be signed in.' },
        { status: 401 }
      ),
    };
  }

  return {
    ok: true,
    ctx: {
      userId: user.id,
      accessToken: accessToken,
      user,
    },
  };
}

// ═══════════════════════════════════════════════════════════
//  Proxy Helper
// ═══════════════════════════════════════════════════════════

/**
 * Forward a request to the Python AI service.
 *
 * Auth strategy (in priority order):
 * 1. If `ctx.accessToken` exists → send as `Authorization: Bearer <jwt>`
 *    (ai_engine validates natively via SUPABASE_JWT_SECRET, RLS enforced)
 * 2. Fallback → `X-User-ID` + `X-Internal-Key` (server-to-server,
 *    ai_engine uses admin client, RLS bypassed but filtered by user_id)
 */
export async function proxyToAI({
  method,
  path,
  ctx,
  body,
  timeoutMs = 30_000,
}) {
  const url = `${AI_SERVICE_URL}${path}`;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  // Build headers — prefer Bearer JWT, fall back to X-User-ID
  const headers = { 'Content-Type': 'application/json' };

  if (ctx.accessToken) {
    headers['Authorization'] = `Bearer ${ctx.accessToken}`;
  } else {
    headers['X-User-ID'] = ctx.userId;
    headers['X-Internal-Key'] = INTERNAL_API_KEY;
  }

  try {
    const response = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    return response;
  } finally {
    clearTimeout(timeout);
  }
}
