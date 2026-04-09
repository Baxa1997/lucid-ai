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
 * GET /api/gitlab/tree?repo=owner/repo&ref=main
 * 
 * Fetches the repository file tree from GitLab using the user's stored credentials.
 * Returns a flat list of file paths for the Code panel's FileExplorer.
 */
export async function GET(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { searchParams } = new URL(req.url);
  const repo = searchParams.get('repo');
  const ref = searchParams.get('ref') || 'main';

  if (!repo) {
    return NextResponse.json({ error: 'Missing repo parameter' }, { status: 400 });
  }

  try {
    // Load GitLab settings from deployment_settings (same as file route)
    const { data: settings } = await getSupabaseAdmin()
      .from('deployment_settings')
      .select('gitlab_host, gitlab_token_enc, gitlab_token_iv')
      .eq('user_id', ctx.user.id)
      .single();

    if (!settings?.gitlab_host || !settings?.gitlab_token_enc) {
      return NextResponse.json({ error: 'GitLab not configured' }, { status: 404 });
    }

    // Decrypt the token
    const { decrypt } = await import('@/lib/crypto');
    const gitlabToken = decrypt(settings.gitlab_token_enc, settings.gitlab_token_iv);

    const host = settings.gitlab_host.replace(/\/+$/, '');
    const projectId = encodeURIComponent(repo);

    // Fetch all pages of the tree (GitLab paginates at 100)
    let allFiles = [];
    let page = 1;
    const maxPages = 5; // Safety cap — 500 files max

    while (page <= maxPages) {
      const url = new URL(`${host}/api/v4/projects/${projectId}/repository/tree`);
      url.searchParams.set('recursive', 'true');
      url.searchParams.set('per_page', '100');
      url.searchParams.set('page', String(page));
      url.searchParams.set('ref', ref);

      const res = await fetch(url.toString(), {
        headers: { 'PRIVATE-TOKEN': gitlabToken },
        signal: AbortSignal.timeout(10000),
      });

      if (!res.ok) {
        if (page === 1) {
          const text = await res.text();
          console.error('[gitlab/tree] Error:', res.status, text);
          return NextResponse.json({ error: `GitLab API error: ${res.status}` }, { status: res.status });
        }
        break; // Partial success is fine
      }

      const items = await res.json();
      if (!items.length) break;

      const filePaths = items
        .filter(item => item.type === 'blob')
        .map(item => item.path);
      allFiles.push(...filePaths);

      // Check pagination headers
      const totalPages = parseInt(res.headers.get('x-total-pages') || '1', 10);
      if (page >= totalPages) break;
      page++;
    }

    return NextResponse.json({ files: allFiles });
  } catch (err) {
    console.error('[gitlab/tree] Exception:', err.message);
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
