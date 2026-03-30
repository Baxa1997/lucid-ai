import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { selectTemplate, cloneTemplate } from '@/services/template.service';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  POST /api/clone-template
//
//  Selects the right Lucid template for the project, clones
//  it into a new private GitHub repo under the platform org,
//  and (optionally) stamps the conversation record.
//
//  Body: {
//    stack:       'react' | 'nextjs' | 'vue' | 'html-css' | 'auto'
//    projectType: 'admin-panel' | 'website' | 'saas' | ...
//    projectName: string          — human name for slug generation
//    conversationId?: string      — if provided, updates conversations row
//  }
//
//  Returns: {
//    ok: true,
//    repoUrl, repoName, cloneUrl, template,
//    templateStack, templateLabel
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

  const { stack, projectType, projectName, conversationId } = body;

  if (!projectName?.trim()) {
    return NextResponse.json({ error: 'projectName is required' }, { status: 400 });
  }

  const githubToken = process.env.PLATFORM_GITHUB_TOKEN;
  if (!githubToken) {
    return NextResponse.json(
      { error: 'PLATFORM_GITHUB_TOKEN is not configured' },
      { status: 500 }
    );
  }

  // ── Select template ──────────────────────────────────────
  const template = selectTemplate(stack, projectType);

  // ── Build repo name: lucid-{userId}-{slug} ───────────────
  const projectSlug = projectName
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 40);

  // Use a short user ID prefix (first 8 chars) to keep names readable
  const userPrefix = (ctx.userId || 'user').slice(0, 8);
  const repoName = `lucid-${userPrefix}-${projectSlug}`;

  // ── Clone the template repo ──────────────────────────────
  let repo;
  try {
    repo = await cloneTemplate(template, repoName, githubToken);
  } catch (err) {
    console.error('[clone-template] Clone failed:', err);
    return NextResponse.json(
      { error: err.message || 'Template clone failed' },
      { status: 502 }
    );
  }

  // ── Optionally update the conversation with repo metadata ─
  if (conversationId) {
    try {
      const supabase = await getSupabaseServerClient();
      await supabase
        .from('conversations')
        .update({
          repo_name: repoName,
          repo_url: repo.repoUrl,
          repo_provider: 'github',
          status: 'template_cloned',
        })
        .eq('id', conversationId)
        .eq('user_id', ctx.userId);
    } catch (err) {
      // Non-fatal — log and continue
      console.warn('[clone-template] Failed to stamp conversation:', err.message);
    }
  }

  return NextResponse.json({
    ok: true,
    repoUrl: repo.repoUrl,
    repoName: repo.repoName,
    cloneUrl: repo.cloneUrl,
    template: repo.template,
    templateStack: template.stack,
    templateLabel: template.label,
  });
}
