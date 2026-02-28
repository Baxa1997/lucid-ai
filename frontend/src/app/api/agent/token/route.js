import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';

/**
 * GET /api/agent/token
 *
 * Returns the current user's Supabase access token (JWT).
 * Used by the workspace page to authenticate WebSocket connections
 * directly to the ai_engine.
 *
 * The ai_engine already validates Supabase JWTs natively — no need
 * to mint a separate token.
 */
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  if (!ctx.accessToken) {
    return NextResponse.json(
      { error: 'NoToken', message: 'Could not retrieve access token from session.' },
      { status: 500 }
    );
  }

  return NextResponse.json({ token: ctx.accessToken });
}
