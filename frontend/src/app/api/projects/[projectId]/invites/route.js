// Lucid AI — proxy: create an invite (POST) and list pending invites (GET).
// Mirrors ai_engine /api/v1/projects/{project_id}/invite (POST) and
// /api/v1/projects/{project_id}/invites (GET).
import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function POST(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { projectId } = await params;
  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  try {
    const res = await proxyToAI({
      method: 'POST',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/invite`,
      ctx,
      body,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to create invite:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable' },
      { status: 503 }
    );
  }
}

export async function GET(_req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { projectId } = await params;

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/projects/${encodeURIComponent(projectId)}/invites`,
      ctx,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('Failed to list project invites:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', invites: [] },
      { status: 503 }
    );
  }
}
