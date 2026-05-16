// Lucid AI — proxy: paginated message history for a project.
// Mirrors ai_engine GET /api/v1/projects/{project_id}/messages.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function GET(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { projectId } = await params;
  const { searchParams } = new URL(req.url);
  const limit = searchParams.get('limit') || '50';
  const offset = searchParams.get('offset') || '0';

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/messages?limit=${limit}&offset=${offset}`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to load project messages:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', messages: [] },
      { status: 503 }
    );
  }
}
