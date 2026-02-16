// ─────────────────────────────────────────────────────────
//  u-code — Gatekeeper Utilities
//  Shared auth-check + forwarding helpers for API routes
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';

/**
 * The base URL of the Python AI microservice.
 * Override via env var in production.
 */
export const AI_SERVICE_URL =
  process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';

// ═══════════════════════════════════════════════════════════
//  Auth Guard
// ═══════════════════════════════════════════════════════════

/**
 * Validate the current session and extract userId + orgId.
 * Returns either { ok: true, ctx: { userId, orgId } } or { ok: false, response: NextResponse }.
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

  if (!session.user.orgId) {
    return {
      ok: false,
      response: NextResponse.json(
        {
          error: 'NoOrganization',
          message: 'User does not belong to any organization.',
        },
        { status: 403 }
      ),
    };
  }

  return {
    ok: true,
    ctx: {
      userId: session.user.id,
      orgId: session.user.orgId,
    },
  };
}

// ═══════════════════════════════════════════════════════════
//  Proxy Helper
// ═══════════════════════════════════════════════════════════

/**
 * Forward a request to the Python AI service with injected
 * identity headers (X-User-ID, X-Org-ID).
 *
 * @param {Object} options
 * @param {string} options.method
 * @param {string} options.path
 * @param {{userId: string, orgId: string}} options.ctx
 * @param {Object} [options.body]
 * @param {number} [options.timeoutMs=30000]
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
        'X-Org-ID': ctx.orgId,
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    return response;
  } finally {
    clearTimeout(timeout);
  }
}
