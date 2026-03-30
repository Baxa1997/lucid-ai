import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  Supported providers and models (mirrors the frontend UI)
// ─────────────────────────────────────────────────────────
const VALID_PROVIDERS = ['anthropic'];

const VALID_MODELS = {
  anthropic: [
    'anthropic/claude-3-5-sonnet-20241022',
    'anthropic/claude-3-5-opus-20241022',
    'anthropic/claude-sonnet-4-6',
    'anthropic/claude-opus-4-6',
  ],
};

/**
 * GET /api/settings
 * Returns the authenticated user's LLM settings.
 * If no row exists yet, returns the default configuration.
 */
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const supabase = await getSupabaseServerClient();

  const { data, error } = await supabase
    .from('user_settings')
    .select('*')
    .eq('user_id', ctx.userId)
    .maybeSingle();

  if (error) {
    console.error('[settings GET] Supabase error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  // Return defaults if no row yet
  if (!data) {
    return NextResponse.json({
      llm_provider: 'anthropic',
      llm_model: 'anthropic/claude-3-5-sonnet-20241022',
      has_api_key: false,
      package_manager: 'npm',
      // Deployment defaults
      gitlab_host: 'https://gitlab.udevs.io',
      gitlab_group: '',
      has_gitlab_token: false,
      has_github_token: false,
      ops_repo_url: 'https://gitlab.udevs.io/ops/deployments',
      ops_repo_branch: 'master',
      has_vercel_token: false,
      vercel_team_id: '',
      k8s_namespace: 'frontend-prod',
      k8s_domain: '*.javoxir.online',
      k8s_tls_secret: '',
      registry_url: 'gitlab.udevs.io:5050',
    });
  }

  return NextResponse.json({
    llm_provider: data.llm_provider,
    llm_model: data.llm_model,
    has_api_key: !!(data.api_key_enc && data.api_key_iv),
    package_manager: data.package_manager || 'npm',
    // Deployment settings (never return raw tokens)
    gitlab_host: data.gitlab_host || 'https://gitlab.udevs.io',
    gitlab_group: data.gitlab_group || '',
    has_gitlab_token: !!(data.gitlab_token_enc && data.gitlab_token_iv),
    has_github_token: !!(data.github_token_enc && data.github_token_iv),
    ops_repo_url: data.ops_repo_url || 'https://gitlab.udevs.io/ops/deployments',
    ops_repo_branch: data.ops_repo_branch || 'master',
    has_vercel_token: !!(data.vercel_token_enc && data.vercel_token_iv),
    vercel_team_id: data.vercel_team_id || '',
    k8s_namespace: data.k8s_namespace || 'frontend-prod',
    k8s_domain: data.k8s_domain || '*.javoxir.online',
    k8s_tls_secret: data.k8s_tls_secret || '',
    registry_url: data.registry_url || 'gitlab.udevs.io:5050',
  });
}

/**
 * PUT /api/settings
 * Upserts the authenticated user's LLM settings.
 *
 * Body: { llm_provider, llm_model, api_key? }
 *
 * The api_key is encrypted on the server before storage using
 * AES-GCM with the ENCRYPTION_KEY environment variable.
 * If api_key is omitted/null, the existing encrypted value is preserved.
 */
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

  const {
    llm_provider, llm_model, api_key,
    package_manager,
    // Deployment fields (optional — only present from Deployment tab)
    gitlab_host, gitlab_group, gitlab_token,
    github_token,
    ops_repo_url, ops_repo_branch,
    vercel_token, vercel_team_id,
    k8s_namespace, k8s_domain, k8s_tls_secret,
    registry_url,
  } = body;

  // LLM fields are required if present
  if (llm_provider !== undefined) {
    if (!VALID_PROVIDERS.includes(llm_provider)) {
      return NextResponse.json(
        { error: `Invalid provider. Must be one of: ${VALID_PROVIDERS.join(', ')}` },
        { status: 400 }
      );
    }
    if (!VALID_MODELS[llm_provider]?.includes(llm_model)) {
      return NextResponse.json(
        { error: `Invalid model '${llm_model}' for provider '${llm_provider}'.` },
        { status: 400 }
      );
    }
  }

  const supabase = await getSupabaseServerClient();

  const { data: existing, error: fetchError } = await supabase
    .from('user_settings')
    .select('*')
    .eq('user_id', ctx.userId)
    .maybeSingle();

  if (fetchError) {
    console.error('[settings PUT] Supabase fetch error:', fetchError);
    return NextResponse.json({ error: fetchError.message }, { status: 500 });
  }

  let updatePayload = {
    user_id: ctx.userId,
    // Preserve existing LLM settings unless overridden
    llm_provider: llm_provider ?? existing?.llm_provider ?? 'anthropic',
    llm_model: llm_model ?? existing?.llm_model ?? 'anthropic/claude-3-5-sonnet-20241022',
    api_key_enc: existing?.api_key_enc || null,
    api_key_iv: existing?.api_key_iv || null,
    // Application settings
    package_manager: package_manager ?? existing?.package_manager ?? 'npm',
    // Preserve existing deployment settings unless overridden
    gitlab_host: gitlab_host ?? existing?.gitlab_host ?? 'https://gitlab.udevs.io',
    gitlab_group: gitlab_group ?? existing?.gitlab_group ?? '',
    gitlab_token_enc: existing?.gitlab_token_enc || null,
    gitlab_token_iv: existing?.gitlab_token_iv || null,
    github_token_enc: existing?.github_token_enc || null,
    github_token_iv: existing?.github_token_iv || null,
    ops_repo_url: ops_repo_url ?? existing?.ops_repo_url ?? 'https://gitlab.udevs.io/ops/deployments',
    ops_repo_branch: ops_repo_branch ?? existing?.ops_repo_branch ?? 'master',
    vercel_token_enc: existing?.vercel_token_enc || null,
    vercel_token_iv: existing?.vercel_token_iv || null,
    vercel_team_id: vercel_team_id ?? existing?.vercel_team_id ?? '',
    k8s_namespace: k8s_namespace ?? existing?.k8s_namespace ?? 'frontend-prod',
    k8s_domain: k8s_domain ?? existing?.k8s_domain ?? '*.javoxir.online',
    k8s_tls_secret: k8s_tls_secret ?? existing?.k8s_tls_secret ?? '',
    registry_url: registry_url ?? existing?.registry_url ?? 'gitlab.udevs.io:5050',
  };

  // Encrypt sensitive tokens
  try {
    const { encrypt } = await import('@/lib/crypto');

    if (api_key?.trim()) {
      const { encrypted, iv } = encrypt(api_key.trim());
      updatePayload.api_key_enc = encrypted;
      updatePayload.api_key_iv = iv;
    }
    if (gitlab_token?.trim()) {
      const { encrypted, iv } = encrypt(gitlab_token.trim());
      updatePayload.gitlab_token_enc = encrypted;
      updatePayload.gitlab_token_iv = iv;
    }
    if (github_token?.trim()) {
      const { encrypted, iv } = encrypt(github_token.trim());
      updatePayload.github_token_enc = encrypted;
      updatePayload.github_token_iv = iv;
    }
    if (vercel_token?.trim()) {
      const { encrypted, iv } = encrypt(vercel_token.trim());
      updatePayload.vercel_token_enc = encrypted;
      updatePayload.vercel_token_iv = iv;
    }
  } catch (encErr) {
    console.error('[settings PUT] Encryption error:', encErr);
    return NextResponse.json(
      { error: 'Failed to encrypt tokens' },
      { status: 500 }
    );
  }

  const { error: upsertError } = await supabase
    .from('user_settings')
    .upsert(updatePayload, { onConflict: 'user_id' });

  if (upsertError) {
    console.error('[settings PUT] Supabase upsert error:', upsertError);
    return NextResponse.json({ error: upsertError.message }, { status: 500 });
  }

  return NextResponse.json({ success: true });
}
