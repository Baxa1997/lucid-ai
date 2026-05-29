import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function GET(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;
  const { provider } = await params;
  const query = new URL(req.url).search;

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/integrations/${provider}/branches${query}`,
      ctx,
      timeoutMs: 25000,
    });

    const text = await res.text();
    if (!res.ok) {
      console.error(`[git-branches] ai_engine ${res.status}:`, text);
      return NextResponse.json({ branches: [], error: text }, { status: res.status });
    }

    return NextResponse.json(JSON.parse(text));
  } catch (err) {
    console.error('[git-branches] Error:', err);
    return NextResponse.json({ branches: [], error: err.message }, { status: 502 });
  }
}
