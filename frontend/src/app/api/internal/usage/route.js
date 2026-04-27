// ─────────────────────────────────────────────────────────
//  POST /api/internal/usage
//
//  Server-to-server endpoint the ai_engine calls after every
//  LLM invocation (project generation, agent steps, etc.) to
//  meter token consumption against the user's monthly quota.
//
//  Auth: requires the shared INTERNAL_API_KEY (set on both
//  frontend + ai_engine via env). Identifies the user by the
//  X-User-ID header.
//
//  Body: { input_tokens: number, output_tokens: number, source?: string }
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import { recordTokenUsage } from '@/lib/usage';

export async function POST(req) {
  const internalKey = req.headers.get('x-internal-key');
  if (!process.env.INTERNAL_API_KEY || internalKey !== process.env.INTERNAL_API_KEY) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const userId = req.headers.get('x-user-id');
  if (!userId) {
    return NextResponse.json({ error: 'Missing X-User-ID' }, { status: 400 });
  }

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const inputTokens  = Number(body.input_tokens  ?? 0);
  const outputTokens = Number(body.output_tokens ?? 0);

  if (!Number.isFinite(inputTokens) || !Number.isFinite(outputTokens)) {
    return NextResponse.json({ error: 'input_tokens and output_tokens must be numbers' }, { status: 400 });
  }

  await recordTokenUsage(userId, inputTokens, outputTokens);
  return NextResponse.json({ ok: true });
}
