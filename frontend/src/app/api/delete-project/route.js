import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  DELETE /api/delete-project
//
//  Deletes a platform-generated project:
//   1. Removes the GitHub repo from the platform org
//   2. Deletes all chat_sessions (and cascaded messages) for the project
//
//  Body: { projectId: string, repoUrl: string }
// ─────────────────────────────────────────────────────────

export async function DELETE(request) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  const { projectId, repoUrl } = body;
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  const errors = [];

  // ── Step 1: Delete the GitHub repo ─────────────────────────────────────────
  if (repoUrl) {
    const platformToken = process.env.PLATFORM_GITHUB_TOKEN;
    if (!platformToken) {
      errors.push('PLATFORM_GITHUB_TOKEN not configured — GitHub repo was not deleted');
    } else {
      // Extract {owner}/{repo} from https://github.com/{owner}/{repo}
      const repoPath = repoUrl.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
      try {
        const ghRes = await fetch(`https://api.github.com/repos/${repoPath}`, {
          method: 'DELETE',
          headers: {
            Authorization: `Bearer ${platformToken}`,
            Accept: 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
          },
        });
        // 204 = deleted, 404 = already gone — both are acceptable
        if (ghRes.status !== 204 && ghRes.status !== 404) {
          const ghBody = await ghRes.text().catch(() => '');
          errors.push(`GitHub delete failed (${ghRes.status}): ${ghBody}`);
        }
      } catch (err) {
        errors.push(`GitHub API error: ${err.message}`);
      }
    }
  }

  // ── Step 2: Delete chat_sessions (and cascaded messages) ───────────────────
  const supabase = await getSupabaseServerClient();
  const { error: dbError } = await supabase
    .from('chat_sessions')
    .delete()
    .eq('project_id', projectId)
    .eq('user_id', ctx.userId);

  if (dbError) {
    errors.push(`DB delete failed: ${dbError.message}`);
  }

  if (errors.length > 0 && dbError) {
    // Only fail hard if the DB delete failed — GitHub errors are non-fatal
    return NextResponse.json({ error: errors.join('; ') }, { status: 500 });
  }

  return NextResponse.json({ ok: true, warnings: errors.length ? errors : undefined });
}
