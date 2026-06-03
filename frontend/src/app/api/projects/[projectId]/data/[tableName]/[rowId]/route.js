// Lucid AI — proxy: update/delete generated collection rows.
// Mirrors ai_engine /api/v1/projects/{project_id}/data/{table_name}/{row_id}.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function PATCH(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;
  const { projectId, tableName, rowId } = await params;
  const body = await req.json().catch(() => ({}));

  try {
    const res = await proxyToAI({
      method: 'PATCH',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/data/${encodeURIComponent(tableName)}/${encodeURIComponent(rowId)}`,
      ctx,
      body,
      timeoutMs: 60_000,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to update project data row:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 },
    );
  }
}

export async function DELETE(_req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;
  const { projectId, tableName, rowId } = await params;

  try {
    const res = await proxyToAI({
      method: 'DELETE',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/data/${encodeURIComponent(tableName)}/${encodeURIComponent(rowId)}`,
      ctx,
      timeoutMs: 60_000,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to delete project data row:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 },
    );
  }
}
