// Lucid AI — proxy: generated DataModel for Settings > Data.
// Mirrors ai_engine GET /api/v1/projects/{project_id}/data-model.
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
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/data-model`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to load project data model:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', tables: [] },
      { status: 503 },
    );
  }
}
