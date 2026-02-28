import { NextResponse } from 'next/server';
import { requireAuth, proxyToAI } from '@/lib/gatekeeper';
import prisma from '@/lib/prisma';
import { decrypt } from '@/lib/crypto';

// ── Environment Variables ─────────────────────────────────
const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';

/**
 * POST /api/agent/start
 * Initializes a new AI Agent session.
 */
export async function POST(req) {
  // ── 1. Auth Check (Supabase) ──────────────────────────
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const userId = ctx.userId;

  // ── 2. Input Validation ────────────────────────────────
  let body;
  try {
    body = await req.json();
  } catch (e) {
    return NextResponse.json({ error: 'InvalidJSON' }, { status: 400 });
  }

  const { projectId, task } = body;

  if (!task) {
    return NextResponse.json(
      { error: 'MissingFields', message: 'task is required.' },
      { status: 400 }
    );
  }

  let project = null;
  let decryptedToken = null;
  let resolvedProjectId = null;

  if (projectId && projectId !== 'scratch-session') {
    // ── 3. Database Lookup: Project ────────────────────────
    project = await prisma.project.findUnique({
      where: { id: projectId },
    });

    if (!project) {
      return NextResponse.json(
        { error: 'NotFound', message: 'Project not found.' },
        { status: 404 }
      );
    }

    // ── 4. Security Check: User Isolation ─────────────────
    if (project.userId !== userId) {
      return NextResponse.json(
        { error: 'Forbidden', message: 'Access denied to this project.' },
        { status: 403 }
      );
    }

    resolvedProjectId = project.id;

    // ── 5. Fetch Integration & Decrypt Token ───────────────
    if (project.provider) {
      const integration = await prisma.integration.findUnique({
        where: {
          userId_provider: {
            userId,
            provider: project.provider,
          },
        },
      });

      if (!integration) {
        return NextResponse.json(
          { error: 'NoIntegration', message: `No integration found for ${project.provider}. Please connect it in settings.` },
          { status: 400 }
        );
      }

      try {
        decryptedToken = decrypt(integration.accessTokenEncrypted, integration.iv);
      } catch (error) {
        console.error('Decryption failed:', error);
        return NextResponse.json(
          { error: 'DecryptionError', message: 'Failed to decrypt access token.' },
          { status: 500 }
        );
      }
    }
  }

  // ── 6. Agent Handoff: POST to Python AI Engine ─────────
  const remotePayload = {
    task,
    repoUrl: project?.repoUrl || body.repoUrl || '',
    gitToken: decryptedToken || body.gitToken || '',
    projectId: resolvedProjectId || '',
    userId,
    model_provider: body.modelProvider || body.model_provider || '',
    api_key: body.apiKey || body.api_key || '',
    gitUserName: ctx.user?.user_metadata?.full_name || ctx.user?.user_metadata?.name || '',
    gitUserEmail: ctx.user?.email || '',
    branch: body.branch || '',
  };

  let agentResponseData;

  try {
    const res = await proxyToAI({
      method: 'POST',
      path: '/api/v1/sessions',
      ctx,
      body: remotePayload,
    });

    if (!res.ok) {
      const errorText = await res.text();
      console.error('Python Backend Error:', errorText);
      return NextResponse.json(
        { error: 'AgentBackendError', message: 'Failed to initialize agent session.', details: errorText },
        { status: res.status }
      );
    }

    agentResponseData = await res.json();
  } catch (error) {
    console.error('Failed to contact Python backend:', error);
    return NextResponse.json(
      { error: 'ServiceUnavailable', message: 'Could not reach the AI Agent service.' },
      { status: 503 }
    );
  }

  // ── 7. Save session record ─────────────────────────────
  const sessionId = agentResponseData.sessionId;

  if (!sessionId) {
    return NextResponse.json(
      { error: 'InvalidBackendResponse', message: 'Backend did not return a sessionId.' },
      { status: 500 }
    );
  }

  await prisma.agentSession.create({
    data: {
      userId,
      projectId: resolvedProjectId,
      agentSessionId: sessionId,
      title: task.slice(0, 50),
      status: 'ACTIVE',
      metadata: {},
    },
  });

  // Return the Supabase access token as the WS token — the ai_engine
  // validates it natively (same SUPABASE_JWT_SECRET).
  const wsUrl = process.env.NEXT_PUBLIC_AGENT_WS_URL || 'ws://localhost:8000/api/v1/ws';

  return NextResponse.json({
    wsUrl,
    sessionToken: ctx.accessToken,
    sessionId,
  });
}
