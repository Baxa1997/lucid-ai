import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { createClient } from '@supabase/supabase-js';

function getSupabaseAdmin() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_ROLE_KEY
  );
}

/**
 * GET /api/gitlab/file?repo=<repo_path>&path=<file_path>
 * Reads a single file from a GitLab repo using the Repository Files API.
 * Falls back to using deployment settings for auth.
 */
export async function GET(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { searchParams } = new URL(req.url);
  const repo = searchParams.get('repo');
  const filePath = searchParams.get('path');

  if (!repo || !filePath) {
    return NextResponse.json({ error: 'Missing repo or path' }, { status: 400 });
  }

  try {
    // Load GitLab settings from deployment_settings
    const { data: settings } = await getSupabaseAdmin()
      .from('deployment_settings')
      .select('gitlab_host, gitlab_token_enc, gitlab_token_iv')
      .eq('user_id', ctx.user.id)
      .maybeSingle();

    if (!settings?.gitlab_host || !settings?.gitlab_token_enc) {
      return NextResponse.json({ error: 'GitLab not configured' }, { status: 404 });
    }

    // Decrypt the token
    const { decrypt } = await import('@/lib/crypto');
    const gitlabToken = decrypt(settings.gitlab_token_enc, settings.gitlab_token_iv);

    // Use GitLab Repository Files API
    const encodedPath = encodeURIComponent(filePath);
    const encodedRepo = encodeURIComponent(repo);
    const apiUrl = `${settings.gitlab_host.replace(/\/+$/, '')}/api/v4/projects/${encodedRepo}/repository/files/${encodedPath}?ref=main`;

    const res = await fetch(apiUrl, {
      headers: {
        'PRIVATE-TOKEN': gitlabToken,
      },
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: `File not found: ${filePath}`, detail: text },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json({
      content: data.content,
      encoding: data.encoding || 'base64',
      file_name: data.file_name,
      file_path: data.file_path,
      size: data.size,
    });
  } catch (err) {
    console.error('[gitlab/file] Error:', err.message);
    return NextResponse.json({ error: 'Failed to read file from GitLab' }, { status: 500 });
  }
}
