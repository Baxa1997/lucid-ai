// Lucid AI — proxy: list current members of a project.
// Mirrors ai_engine GET /api/v1/projects/{project_id}/members.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function GET(_req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { projectId } = await params;

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/members`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to list project members:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', members: [] },
      { status: 503 }
    );
  }
}
