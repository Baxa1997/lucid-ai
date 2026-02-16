import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';
import prisma from '@/lib/prisma';
import { decrypt } from '@/lib/crypto';
import jwt from 'jsonwebtoken';

// ── Environment Variables ─────────────────────────────────
const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
const SESSION_SECRET = process.env.SESSION_SECRET;

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
  
  if (!session?.user) {
    return NextResponse.json(
      { error: 'Unauthorized', message: 'You must be logged in.' },
      { status: 401 }
    );
  }

  const { id: userId, orgId } = session.user;

  if (!orgId) {
    return NextResponse.json(
      { error: 'NoOrganization', message: 'User does not belong to an organization.' },
      { status: 403 }
    );
  }

  // ── 2. Input Validation ────────────────────────────────
  let body;
  try {
    body = await req.json();
  } catch (e) {
    return NextResponse.json({ error: 'InvalidJSON' }, { status: 400 });
  }

  const { projectId, task } = body;

  if (!projectId || !task) {
    return NextResponse.json(
      { error: 'MissingFields', message: 'projectId and task are required.' },
      { status: 400 }
    );
  }

  let project = null;
  let decryptedToken = null;

  // ── Special Case: Scratch / Demo Session ────────────────
  if (projectId === 'scratch-session') {
    // Skip DB lookups and create a dummy project context
    project = {
      id: 'scratch-session',
      repoUrl: null, // No repo for scratch session
      provider: null,
      orgId: orgId, // Assume ownership
    };
    decryptedToken = null; // No git token needed
  } else {
    // ── 3. Database Lookup: Project ────────────────────────
    project = await prisma.project.findUnique({
      where: { id: projectId },
      include: { org: true }, 
    });

    if (!project) {
      return NextResponse.json(
        { error: 'NotFound', message: 'Project not found.' },
        { status: 404 }
      );
    }

    // ── 4. Security Check: Tenant Isolation ────────────────
    if (project.orgId !== orgId) {
      return NextResponse.json(
        { error: 'Forbidden', message: 'Access denied to this project.' },
        { status: 403 }
      );
    }

    // ── 5. Fetch Integration & Decrypt Token ───────────────
    if (project.provider) {
      const integration = await prisma.integration.findUnique({
        where: {
          orgId_provider: {
            orgId,
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

  // ── 6. Agent Handoff: POST to Python Backend ───────────
  // Construct the payload for the Python service
  // SandboxId format: user_[ID] (as requested)
  // Ensure we sanitize or use the user ID directly.
  const sandboxId = `user_${userId.replace(/[^a-zA-Z0-9]/g, '')}`; 

  const remotePayload = {
    userId,
    repoUrl: project.repoUrl,
    gitToken: decryptedToken,
    task,
    sandboxId,
    // Optional: Pass project info if needed by backend
    projectId: project.id,
  };

  let agentResponseData;

  try {
    const agentRes = await fetch(`${PYTHON_BACKEND_URL}/api/session/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

  // ── 7. Token Generation (JWT) ──────────────────────────
  // payload: { userId, projectId, sessionId }
  const sessionId = agentResponseData.sessionId || agentResponseData.id; // Adjust based on actual Python response

  if (!sessionId) {
    return NextResponse.json(
      { error: 'InvalidBackendResponse', message: 'Backend did not return a sessionId.' },
      { status: 500 }
    );
  }

  // Create local record too (optional but good practice)
  await prisma.agentSession.create({
    data: {
      userId,
      projectId,
      agentSessionId: sessionId,
      title: task.slice(0, 50),
      status: 'ACTIVE',
      metadata: { sandboxId },
    },
  });

  const sessionToken = jwt.sign(
    { userId, projectId, sessionId },
    SESSION_SECRET || 'fallback_secret_do_not_use_in_prod',
    { expiresIn: '1h' }
  );

  // ── 8. Return Response ─────────────────────────────────
  // Use simple WebSocket URL or proxy URL
  // If running via Next.js proxy rewrite:
  // const wsUrl = `ws://${req.headers.get('host')}/api/ws`; 
  // But user requested direct: "ws://localhost:8000/ws"
  const wsUrl = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws';

  return NextResponse.json({
    wsUrl,
    sessionToken,
    sessionId,
  });
}
