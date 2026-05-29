import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

async function forwardIntegrationRequest({ method, ctx, body }) {
  const res = await proxyToAI({
    method,
    path: '/api/v1/integrations',
    ctx,
    body,
    timeoutMs: method === 'POST' ? 30000 : 15000,
  });

  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text || 'Integration request failed' };
  }

  return NextResponse.json(data, { status: res.status });
}

export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  return forwardIntegrationRequest({ method: 'GET', ctx: authResult.ctx });
}

export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;

  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  return forwardIntegrationRequest({ method: 'POST', ctx: authResult.ctx, body });
}
