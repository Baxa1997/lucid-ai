import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { provisionSupabase, generateSupabaseClientFile, generateSupabaseAuthFile, generateAuthButtonsFile } from '@/lib/supabase/provision';

// ─────────────────────────────────────────────────────────
//  POST /api/provision-supabase
//  Provisions a new Supabase project for a wizard build.
//
//  Body: { projectName, projectSlug, projectType, platformProjectId }
//  Returns: { ok, supabaseUrl, supabaseAnonKey, supabaseRef, method,
//             clientFileContent, authFileContent, authButtonsFileContent,
//             suggestedProviders }
// ─────────────────────────────────────────────────────────

export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const { projectName, projectSlug, projectType, platformProjectId } = body;

  if (!projectName?.trim()) {
    return NextResponse.json(
      { error: 'projectName is required' },
      { status: 400 }
    );
  }

  try {
    const result = await provisionSupabase({
      projectName: projectName.trim(),
      projectSlug: (projectSlug || projectName).trim().toLowerCase().replace(/[^a-z0-9-]/g, '-'),
      projectType: projectType || 'default',
      userId: ctx.userId,
      platformProjectId: platformProjectId || null,
    });

    if (!result.ok) {
      return NextResponse.json(
        { error: result.error || 'Provisioning failed' },
        { status: 502 }
      );
    }

    return NextResponse.json({
      ok: true,
      method: result.method,
      supabaseUrl: result.supabaseUrl,
      supabaseAnonKey: result.supabaseAnonKey,
      supabaseRef: result.supabaseRef,
      tables: result.tables,
      authProviders: result.authProviders,
      suggestedProviders: result.suggestedProviders,
      clientFileContent: result.clientFileContent,
      authFileContent: result.authFileContent,
      authButtonsFileContent: result.authButtonsFileContent,
      envFileContent: result.envFileContent,
    });
  } catch (err) {
    console.error('[provision-supabase] Error:', err);
    return NextResponse.json(
      { error: 'Internal provisioning error' },
      { status: 500 }
    );
  }
}
