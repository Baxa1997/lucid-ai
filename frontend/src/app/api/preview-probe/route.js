import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

/**
 * GET /api/preview-probe?url=<absolute-url>
 *
 * Server-side liveness check for a preview URL. Wraps ai_engine
 * GET /api/v1/preview/probe so the browser can bypass CORS and — more
 * importantly — see the real HTTP status code, which a cross-origin
 * `fetch(..., {mode:'no-cors'})` opaque response hides.
 */
export async function GET(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { searchParams } = new URL(req.url);
  const target = searchParams.get('url');
  if (!target) {
    return NextResponse.json({ error: 'Missing url' }, { status: 400 });
  }

  try {
    const res = await proxyToAI({
      method: 'GET',
      path: `/api/v1/preview/probe?url=${encodeURIComponent(target)}`,
      ctx,
    });

    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.ok ? 200 : res.status });
  } catch (err) {
    console.error('Preview probe failed:', err);
    // Treat proxy failure as unknown — caller should fall back to local.
    return NextResponse.json(
      { alive: false, status: 0, reason: 'proxy_error' },
      { status: 503 },
    );
  }
}
