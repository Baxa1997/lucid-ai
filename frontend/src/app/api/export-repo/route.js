import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { exportRepo } from '@/lib/deploy';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  POST /api/export-repo
//  Transfers a project's GitLab repo to the user's
//  connected GitLab/GitHub account.
//
//  Body: { platformProjectId, targetNamespace }
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

  const { platformProjectId, targetNamespace } = body;

  if (!platformProjectId || !targetNamespace) {
    return NextResponse.json(
      { error: 'platformProjectId and targetNamespace are required' },
      { status: 400 }
    );
  }

  try {
    // Look up the deployment record to get the GitLab project ID
    const supabase = await getSupabaseServerClient();
    const { data: deployment, error: dbErr } = await supabase
      .from('project_deployments')
      .select('gitlab_project_id')
      .eq('user_id', ctx.userId)
      .eq('project_id', platformProjectId)
      .maybeSingle();

    if (dbErr || !deployment?.gitlab_project_id) {
      return NextResponse.json(
        { error: 'No deployment found for this project' },
        { status: 404 }
      );
    }

    // Get user's deployment settings
    const settingsRes = await fetch(
      new URL('/api/settings', req.url).toString(),
      { headers: req.headers }
    );
    const settings = settingsRes.ok ? await settingsRes.json() : {};

    if (!settings.gitlab_host || !settings.gitlab_token) {
      return NextResponse.json(
        { error: 'GitLab settings not configured' },
        { status: 400 }
      );
    }

    const result = await exportRepo({
      gitlabProjectId: deployment.gitlab_project_id,
      targetNamespace,
      settings,
    });

    return NextResponse.json({
      ok: true,
      newUrl: result.newUrl,
    });
  } catch (err) {
    console.error('[export-repo] Error:', err);
    return NextResponse.json(
      { error: `Export failed: ${err.message}` },
      { status: 500 }
    );
  }
}
