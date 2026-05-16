// Lucid AI — proxy: remove a member from a project (owner-only).
// Mirrors ai_engine DELETE /api/v1/projects/{project_id}/members/{user_id}.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function DELETE(_req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { projectId, memberId } = await params;

  try {
    const res = await proxyToAI({
      method: 'DELETE',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(memberId)}`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to remove member:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 }
    );
  }
}
