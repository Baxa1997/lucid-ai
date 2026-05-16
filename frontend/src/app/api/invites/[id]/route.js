// Lucid AI — proxy: revoke an invitation by id.
// Mirrors ai_engine DELETE /api/v1/invites/{invite_id}.
//
// Note: the dynamic segment is named `[id]` for Next.js routing reasons —
// it shares a folder with `[id]/accept` (which uses the same segment as a
// token). The router below sends it through as the invite_id path param.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function DELETE(_req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { id } = await params;

  try {
    const res = await proxyToAI({
      method: 'DELETE',
      path: `/api/v1/invites/${encodeURIComponent(id)}`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to revoke invite:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 }
    );
  }
}
