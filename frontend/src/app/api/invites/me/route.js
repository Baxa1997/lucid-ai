// Lucid AI — proxy: GET pending invites for the current user.
// Mirrors ai_engine GET /api/v1/invites/me.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: '/api/v1/invites/me',
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to fetch invites:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', invites: [] },
      { status: 503 }
    );
  }
}
