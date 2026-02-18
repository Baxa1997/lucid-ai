import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';

const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';

/**
 * GET /api/files/read?session_id=X&path=Y
 * Proxies to ai_engine GET /api/v1/files/read with server-side auth.
 */
export async function GET(req) {
  const session = await auth();

  if (!session?.user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

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
    const url = `${PYTHON_BACKEND_URL}/api/v1/files/read?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`;
    const res = await fetch(url, {
      headers: {
        'X-User-ID': session.user.id,
        'X-Internal-Key': INTERNAL_API_KEY,
      },
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
