import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function DELETE(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;
  const { provider } = await params;

  try {
    const res = await proxyToAI({
      method: 'DELETE',
      path: `/api/v1/integrations/${provider}`,
      ctx,
      timeoutMs: 15000,
    });

    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { error: text || 'Disconnect failed' };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    console.error('[integrations] DELETE error:', err);
    return NextResponse.json({ error: err.message }, { status: 502 });
  }
}
