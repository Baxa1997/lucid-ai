// ─────────────────────────────────────────────────────────
//  u-code — GET /api/agent/socket
//  WebSocket Proxy — Session-Validated Upgrade
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import prisma from '@/lib/prisma';
import { requireAuth } from '@/lib/gatekeeper';

export async function GET(req) {
  // ── 1. Auth Check ──────────────────────────────────────
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  // ── 2. Get sessionId from query ────────────────────────
  const { searchParams } = new URL(req.url);
  const sessionId = searchParams.get('sessionId');

  if (!sessionId) {
    return NextResponse.json(
      { error: 'MissingField', message: '`sessionId` query param is required.' },
      { status: 400 }
    );
  }

  // ── 3. Verify session ownership ────────────────────────
  const agentSession = await prisma.agentSession.findFirst({
    where: {
      id: sessionId,
      userId: ctx.userId,         // must own the session
      status: 'ACTIVE',           // must be active
      project: {
        orgId: ctx.orgId,         // tenant isolation
      },
    },
    select: {
      id: true,
      agentSessionId: true,
    },
  });

  if (!agentSession) {
    return NextResponse.json(
      { error: 'NotFound', message: 'Session not found, not active, or access denied.' },
      { status: 404 }
    );
  }

  if (!agentSession.agentSessionId) {
    return NextResponse.json(
      { error: 'NoAgentSession', message: 'This session does not have an active agent runtime.' },
      { status: 409 }
    );
  }

  // ── 4. Build the WebSocket URL ─────────────────────────
  const aiWsBase = (process.env.AI_SERVICE_WS_URL || 'ws://localhost:8000');
  const wsUrl = `${aiWsBase}/ws/${agentSession.agentSessionId}?userId=${ctx.userId}&orgId=${ctx.orgId}`;

  // ── 5. Return the connection details ───────────────────
  return NextResponse.json({
    success: true,
    sessionId: agentSession.id,
    agentSessionId: agentSession.agentSessionId,
    wsUrl,
  });
}
