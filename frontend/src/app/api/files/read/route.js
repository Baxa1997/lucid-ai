import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

/**
 * GET /api/files/read?session_id=X&path=Y
 * Proxies to ai_engine GET /api/v1/files/read with Supabase auth.
 */
export async function GET(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { searchParams } = new URL(req.url);
  const sessionId = searchParams.get('session_id');
  const path = searchParams.get('path');

  if (!sessionId || !path) {
    return NextResponse.json(
      { error: 'Missing session_id or path' },
      { status: 400 }
    );
  }

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/files/read?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
      ctx,
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: text }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    console.error('Failed to read file from ai_engine:', err);
    return NextResponse.json({ error: 'ServiceUnavailable' }, { status: 503 });
  }
}
