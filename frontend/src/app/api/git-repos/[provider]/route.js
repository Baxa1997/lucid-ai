import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

export async function GET(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { provider } = await params;

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/integrations/${provider}/repos`,
      ctx,
      timeoutMs: 25000,
    });

    const text = await res.text();
    if (!res.ok) {
      console.error(`[git-repos] ai_engine ${res.status}:`, text);
      return NextResponse.json({ repos: [], error: text }, { status: res.status });
    }

    return NextResponse.json(JSON.parse(text));
  } catch (err) {
    console.error('[git-repos] Error:', err);
    return NextResponse.json({ repos: [], error: err.message }, { status: 502 });
  }
}
