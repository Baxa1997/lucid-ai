import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';
import prisma from '@/lib/prisma';
import { decrypt } from '@/lib/crypto';
import jwt from 'jsonwebtoken';

// ── Environment Variables ─────────────────────────────────
const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
const SESSION_SECRET = process.env.SESSION_SECRET;
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';

if (!SESSION_SECRET) {
  console.warn('⚠️ SESSION_SECRET is not set. WebSocket tokens will be insecure.');
}

/**
 * POST /api/agent/start
 * Initializes a new AI Agent session.
 */
export async function POST(req) {
  // ── 1. Auth Check ──────────────────────────────────────
  const session = await auth();

  if (!session?.user?.id) {
    return NextResponse.json(
      { error: 'Unauthorized', message: 'You must be logged in.' },
      { status: 401 }
    );
  }

  const userId = session.user.id;

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
    gitUserName: session.user.name || '',
    gitUserEmail: session.user.email || '',
    branch: body.branch || '',
  };

  let agentResponseData;

  try {
    const agentRes = await fetch(`${PYTHON_BACKEND_URL}/api/v1/sessions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-User-ID': userId,
        'X-Internal-Key': INTERNAL_API_KEY,
      },
      body: JSON.stringify(remotePayload),
    });

    if (!agentRes.ok) {
      const errorText = await agentRes.text();
      console.error('Python Backend Error:', errorText);
      return NextResponse.json(
        { error: 'AgentBackendError', message: 'Failed to initialize agent session.', details: errorText },
        { status: agentRes.status }
      );
    }

    agentResponseData = await agentRes.json();
  } catch (error) {
    console.error('Failed to contact Python backend:', error);
    return NextResponse.json(
      { error: 'ServiceUnavailable', message: 'Could not reach the AI Agent service.' },
      { status: 503 }
    );
  }

  // ── 7. Save session record & generate JWT ──────────────
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

  const sessionToken = jwt.sign(
    { userId, projectId: resolvedProjectId, sessionId },
    SESSION_SECRET || 'fallback_secret_do_not_use_in_prod',
    { expiresIn: '1h' }
  );

  const wsUrl = process.env.NEXT_PUBLIC_AGENT_WS_URL || 'ws://localhost:8000/api/v1/ws';

  return NextResponse.json({
    wsUrl,
    sessionToken,
    sessionId,
  });
}
