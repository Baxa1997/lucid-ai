// ─────────────────────────────────────────────────────────
//  Lucid AI — GET/DELETE /api/agent/[sessionId]
//  Session Status + Stop
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import prisma from '@/lib/prisma';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';

// ═══════════════════════════════════════════════════════════
//  GET — Fetch session status
// ═══════════════════════════════════════════════════════════

export async function GET(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const session = await prisma.agentSession.findFirst({
    where: {
      id: params.sessionId,
      userId: ctx.userId,
      project: { orgId: ctx.orgId },
    },
    include: {
      project: { select: { id: true, name: true, repoUrl: true } },
    },
  });

  if (!session) {
    return NextResponse.json(
      { error: 'NotFound', message: 'Session not found or access denied.' },
      { status: 404 }
    );
  }

  return NextResponse.json({ success: true, session });
}

// ═══════════════════════════════════════════════════════════
//  DELETE — Stop an active session
// ═══════════════════════════════════════════════════════════

export async function DELETE(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const session = await prisma.agentSession.findFirst({
    where: {
      id: params.sessionId,
      userId: ctx.userId,
      status: 'ACTIVE',
      project: { orgId: ctx.orgId },
    },
  });

  if (!session) {
    return NextResponse.json(
      { error: 'NotFound', message: 'Active session not found or access denied.' },
      { status: 404 }
    );
  }

  // Forward stop request to Python AI Engine
  if (session.agentSessionId) {
    try {
      await proxyToAI({
        method: 'DELETE',
        path: `/api/v1/sessions/${session.agentSessionId}`,
        ctx,
        timeoutMs: 15_000,
      });
    } catch (err) {
      console.error('[Gatekeeper] Failed to stop AI session:', err);
      // Continue — still mark as completed in our DB
    }
  }

  // Update our DB record
  const updated = await prisma.agentSession.update({
    where: { id: session.id },
    data: {
      status: 'COMPLETED',
      completedAt: new Date(),
    },
  });

  return NextResponse.json({
    success: true,
    session: {
      id: updated.id,
      status: updated.status,
      completedAt: updated.completedAt,
    },
  });
}
