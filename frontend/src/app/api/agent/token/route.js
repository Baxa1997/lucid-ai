import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';
import jwt from 'jsonwebtoken';

const SESSION_SECRET = process.env.SESSION_SECRET;

/**
 * GET /api/agent/token
 * Returns a short-lived JWT for the current user.
 * Used by the workspace page to authenticate WebSocket connections.
 */
export async function GET() {
  const session = await auth();

  if (!session?.user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const token = jwt.sign(
    { userId: session.user.id, sub: session.user.id },
    SESSION_SECRET || 'fallback_secret_do_not_use_in_prod',
    { expiresIn: '2h' }
  );

  return NextResponse.json({ token });
}
