// Lucid AI — proxy: list/create generated collection rows.
// Mirrors ai_engine /api/v1/projects/{project_id}/data/{table_name}.
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function GET(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;
  const { projectId, tableName } = await params;
  const qs = new URL(req.url).searchParams.toString();

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/data/${encodeURIComponent(tableName)}${qs ? `?${qs}` : ''}`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to list project data rows:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', rows: [] },
      { status: 503 },
    );
  }
}

export async function POST(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;
  const { projectId, tableName } = await params;
  const body = await req.json().catch(() => ({}));

  try {
    const res = await proxyToAI({
      method: 'POST',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/data/${encodeURIComponent(tableName)}`,
      ctx,
      body,
      timeoutMs: 60_000,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to create project data row:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 },
    );
  }
}
