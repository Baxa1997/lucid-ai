import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';
import { encrypt, decrypt } from '@/lib/crypto';
import { createGitLabClient } from '@/lib/gitlab';

const SUPABASE_MGMT_API = 'https://api.supabase.com/v1';

// ─────────────────────────────────────────────────────────
//  GET /api/auth-providers
//  Returns user's Supabase projects with auth provider status.
//
//  PUT /api/auth-providers
//  Saves OAuth credentials for a provider:
//    1. PATCHes Supabase Management API auth config
//    2. Updates GitLab CI variable
//    3. Triggers GitLab pipeline to redeploy
//    4. Updates auth_providers in supabase_projects
// ─────────────────────────────────────────────────────────

export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const supabase = await getSupabaseServerClient();

  const { data, error } = await supabase
    .from('supabase_projects')
    .select('id, project_id, project_slug, supabase_ref, supabase_url, status, auth_providers, suggested_providers, gitlab_project_id, created_at')
    .eq('user_id', ctx.userId)
    .is('deleted_at', null)
    .order('created_at', { ascending: false });

  if (error) {
    console.error('[auth-providers] DB error:', error.message);
    return NextResponse.json({ error: 'Failed to load projects' }, { status: 500 });
  }

  return NextResponse.json({ projects: data || [] });
}

export async function PUT(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const { supabaseProjectId, provider, clientId, clientSecret } = body;

  if (!supabaseProjectId || !provider || !clientId?.trim() || !clientSecret?.trim()) {
    return NextResponse.json(
      { error: 'supabaseProjectId, provider, clientId, and clientSecret are required' },
      { status: 400 }
    );
  }

  const supabase = await getSupabaseServerClient();

  // ── 1. Load the Supabase project record ────────────
  const { data: project, error: fetchErr } = await supabase
    .from('supabase_projects')
    .select('*')
    .eq('id', supabaseProjectId)
    .eq('user_id', ctx.userId)
    .single();

  if (fetchErr || !project) {
    return NextResponse.json({ error: 'Project not found' }, { status: 404 });
  }

  const managementKey =
    process.env.SUPABASE_ACCESS_TOKEN ||
    process.env.SUPABASE_MANAGEMENT_API_KEY;

  if (!managementKey) {
    return NextResponse.json(
      { error: 'Platform Supabase management key not configured' },
      { status: 500 }
    );
  }

  const providerLower = provider.toLowerCase();

  // ── 2. PATCH Supabase auth config to enable provider ───
  try {
    const authPatch = {};
    authPatch[`external_${providerLower}_enabled`] = true;
    authPatch[`external_${providerLower}_client_id`] = clientId.trim();
    authPatch[`external_${providerLower}_secret`] = clientSecret.trim();

    const patchRes = await fetch(`${SUPABASE_MGMT_API}/projects/${project.supabase_ref}/config/auth`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${managementKey}`,
      },
      body: JSON.stringify(authPatch),
      signal: AbortSignal.timeout(15000),
    });

    if (!patchRes.ok) {
      const errText = await patchRes.text();
      throw new Error(`Supabase PATCH failed (${patchRes.status}): ${errText}`);
    }

    console.log(`[auth-providers] ✓ ${provider} enabled on Supabase project ${project.supabase_ref}`);
  } catch (err) {
    console.error('[auth-providers] Supabase config error:', err.message);
    return NextResponse.json(
      { error: `Failed to enable ${provider} on Supabase: ${err.message}` },
      { status: 502 }
    );
  }

  // ── 3. Update GitLab CI variable ───────────────────
  if (project.gitlab_project_id) {
    try {
      // Load deployment settings to get GitLab token
      const { data: settings } = await supabase
        .from('user_settings')
        .select('gitlab_host, gitlab_token_enc, gitlab_token_iv')
        .eq('user_id', ctx.userId)
        .single();

      if (settings?.gitlab_host && settings?.gitlab_token_enc) {
        const gitlabToken = decrypt(settings.gitlab_token_enc, settings.gitlab_token_iv);
        const gitlab = createGitLabClient({ host: settings.gitlab_host, token: gitlabToken });

        const envVarKey = `VITE_${providerLower.toUpperCase()}_ENABLED`;

        await gitlab.setVariable({
          projectId: project.gitlab_project_id,
          key: envVarKey,
          value: 'true',
        });
        console.log(`[auth-providers] ✓ GitLab var ${envVarKey} set to true`);

        // ── 4. Trigger pipeline to redeploy ──────────
        try {
          await gitlab.triggerPipeline({
            projectId: project.gitlab_project_id,
            ref: 'main',
          });
          console.log('[auth-providers] ✓ GitLab pipeline triggered');
        } catch (pipeErr) {
          console.warn('[auth-providers] Pipeline trigger failed (non-fatal):', pipeErr.message);
        }
      }
    } catch (err) {
      console.warn('[auth-providers] GitLab update failed (non-fatal):', err.message);
    }
  }

  // ── 5. Update auth_providers in DB ─────────────────
  const currentProviders = Array.isArray(project.auth_providers) ? project.auth_providers : [];
  const existingIdx = currentProviders.findIndex((p) => p.provider === providerLower);

  const providerEntry = {
    provider: providerLower,
    enabled: true,
    configured_at: new Date().toISOString(),
  };

  if (existingIdx >= 0) {
    currentProviders[existingIdx] = providerEntry;
  } else {
    currentProviders.push(providerEntry);
  }

  // Also update suggested_providers to mark as enabled
  const suggested = Array.isArray(project.suggested_providers) ? project.suggested_providers : [];
  const sugIdx = suggested.findIndex((s) => s.provider === providerLower);
  if (sugIdx >= 0) {
    suggested[sugIdx].enabled = true;
  }

  const { error: updateErr } = await supabase
    .from('supabase_projects')
    .update({
      auth_providers: currentProviders,
      suggested_providers: suggested,
    })
    .eq('id', supabaseProjectId);

  if (updateErr) {
    console.error('[auth-providers] DB update error:', updateErr.message);
    return NextResponse.json({ error: 'Failed to update provider status' }, { status: 500 });
  }

  return NextResponse.json({
    ok: true,
    message: `${provider} OAuth is now live on your project`,
    provider: providerEntry,
  });
}
