import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { deployProject } from '@/lib/deploy';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  POST /api/deploy-project
//  Deploys a generated project: creates repo, pushes code,
//  sets up ops, deploys, and returns the live URL.
//
//  Body: {
//    projectSlug, stack, generatedFiles,
//    platformProjectId, settings (optional)
//  }
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

  const { projectSlug, stack, generatedFiles, platformProjectId, settings } = body;

  if (!projectSlug?.trim()) {
    return NextResponse.json({ error: 'projectSlug is required' }, { status: 400 });
  }

  // ── Fetch user_settings directly from DB and decrypt tokens ──
  // The /api/settings endpoint intentionally never returns decrypted tokens,
  // so we must read from the DB directly and decrypt here for the deploy flow.
  let deploySettings = settings || {};
  try {
    const supabase = await getSupabaseServerClient();
    const { data: savedSettings } = await supabase
      .from('user_settings')
      .select('*')
      .eq('user_id', ctx.userId)
      .maybeSingle();

    if (savedSettings) {
      const { decrypt } = await import('@/lib/crypto');

      // Merge non-sensitive fields
      deploySettings = {
        ...deploySettings,
        gitlab_host: savedSettings.gitlab_host || deploySettings.gitlab_host || '',
        gitlab_group: savedSettings.gitlab_group || deploySettings.gitlab_group || '',
        ops_repo_url: savedSettings.ops_repo_url || deploySettings.ops_repo_url || '',
        ops_repo_branch: savedSettings.ops_repo_branch || deploySettings.ops_repo_branch || 'main',
        vercel_team_id: savedSettings.vercel_team_id || deploySettings.vercel_team_id || '',
        k8s_namespace: savedSettings.k8s_namespace || deploySettings.k8s_namespace || 'frontend-prod',
        k8s_domain: savedSettings.k8s_domain || deploySettings.k8s_domain || '*.udevs.io',
        k8s_tls_secret: savedSettings.k8s_tls_secret || deploySettings.k8s_tls_secret || '',
        registry_url: savedSettings.registry_url || deploySettings.registry_url || '',
      };

      // Decrypt sensitive tokens
      if (savedSettings.gitlab_token_enc && savedSettings.gitlab_token_iv) {
        try {
          deploySettings.gitlab_token = decrypt(savedSettings.gitlab_token_enc, savedSettings.gitlab_token_iv);
        } catch (e) { console.warn('[deploy-project] Failed to decrypt gitlab_token:', e.message); }
      }
      if (savedSettings.vercel_token_enc && savedSettings.vercel_token_iv) {
        try {
          deploySettings.vercel_token = decrypt(savedSettings.vercel_token_enc, savedSettings.vercel_token_iv);
        } catch (e) { console.warn('[deploy-project] Failed to decrypt vercel_token:', e.message); }
      }
    }

    // Platform GitHub token — this is the PLATFORM OWNER's token (env var),
    // NOT the end-user's integration token. New repos are created in the
    // platform's GitHub account. Users export to their own account later.
    const platformGithubToken = process.env.PLATFORM_GITHUB_TOKEN || '';
    if (platformGithubToken) {
      deploySettings.github_token = platformGithubToken;
    }
  } catch (err) {
    console.warn('[deploy-project] Settings load failed, using provided settings:', err.message);
  }

  try {
    const result = await deployProject({
      userId: ctx.userId,
      projectSlug: projectSlug.trim().toLowerCase().replace(/[^a-z0-9-]/g, '-'),
      stack: stack || 'nextjs',
      generatedFiles: generatedFiles || [],
      settings: deploySettings,
      platformProjectId: platformProjectId || null,
    });

    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: 502 });
    }

    return NextResponse.json({
      ok: true,
      repoUrl: result.repoUrl,
      deployUrl: result.deployUrl,
      opsCreated: result.opsCreated,
      method: result.method,
      gitlabProjectId: result.gitlabProjectId || null,
    });
  } catch (err) {
    console.error('[deploy-project] Error:', err);
    return NextResponse.json({ error: 'Deployment failed' }, { status: 500 });
  }
}
