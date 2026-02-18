import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';

/**
 * GET /api/chats
 * Proxies to ai_engine GET /api/v1/chats with server-side auth.
 */
export async function GET(req) {
  const session = await auth();

  if (!session?.user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const limit = searchParams.get('limit') || '50';
  const offset = searchParams.get('offset') || '0';

  try {
    const res = await fetch(
      `${PYTHON_BACKEND_URL}/api/v1/chats?limit=${limit}&offset=${offset}`,
      {
        headers: {
          'X-User-ID': session.user.id,
          'X-Internal-Key': INTERNAL_API_KEY,
        },
      }
    );

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
