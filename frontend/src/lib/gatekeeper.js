// ─────────────────────────────────────────────────────────
//  Lucid AI — Gatekeeper Utilities
//  Shared auth-check + forwarding helpers for API routes
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';

export const AI_SERVICE_URL =
  process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';

const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';

// ═══════════════════════════════════════════════════════════
//  Auth Guard
// ═══════════════════════════════════════════════════════════

/**
 * Validate the current session and extract userId.
 * Returns either { ok: true, ctx: { userId } } or { ok: false, response: NextResponse }.
 */
export async function requireAuth() {
  const session = await auth();

  if (!session?.user?.id) {
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
    ctx: { userId: session.user.id },
  };
}

// ═══════════════════════════════════════════════════════════
//  Proxy Helper
// ═══════════════════════════════════════════════════════════

/**
 * Forward a request to the Python AI service with injected
 * identity headers (X-User-ID, X-Internal-Key).
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

  try {
    const response = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-User-ID': ctx.userId,
        'X-Internal-Key': INTERNAL_API_KEY,
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    return response;
  } finally {
    clearTimeout(timeout);
  }
}
