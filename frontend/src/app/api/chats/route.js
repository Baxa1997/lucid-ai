import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI, AI_SERVICE_URL } from '@/lib/gatekeeper';

/**
 * GET /api/chats
 * Proxies to ai_engine GET /api/v1/chats with Supabase auth.
 */
export async function GET(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { searchParams } = new URL(req.url);
  const limit = searchParams.get('limit') || '50';
  const offset = searchParams.get('offset') || '0';

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/chats?limit=${limit}&offset=${offset}`,
      ctx,
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: text }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    console.error('Failed to fetch chats from ai_engine:', err);
    return NextResponse.json(
      { error: 'ServiceUnavailable', chats: [] },
      { status: 503 }
    );
  }
}
