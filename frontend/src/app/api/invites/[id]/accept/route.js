// Lucid AI — proxy: accept an invitation by token.
// Mirrors ai_engine POST /api/v1/invites/{token}/accept.
//
// The dynamic segment is named `[id]` (shared with the revoke route),
// but downstream it's treated as a token in the accept-invite flow.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function POST(_req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { id: token } = await params;

  try {
    const res = await proxyToAI({
      method: 'POST',
      path: `/api/v1/invites/${encodeURIComponent(token)}/accept`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to accept invite:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 }
    );
  }
}
