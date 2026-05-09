import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  GET /api/platform-repos
//
//  Returns list of platform-generated repos for the current user.
//  These are repos created by the New Project Wizard (hosted on
//  the platform's GitHub account: LucidSoftware-tech).
//
//  Frontend uses this to show "My Projects" section when
//  opening a new conversation — allowing users to continue
//  working on previously-generated projects.
// ─────────────────────────────────────────────────────────

export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  try {
    const supabase = await getSupabaseServerClient();

    // Query chat_sessions for this user. Show ALL projects — drafts that
    // haven't been exported, AND sessions that pushed to GitHub even if
    // their project_id row was never populated (legacy / repair path).
    // We filter client-side because PostgREST lacks an OR-NULL operator
    // that's clean to chain with .not().
    const { data: sessions, error } = await supabase
      .from('chat_sessions')
      .select('id, project_id, title, platform_repo_url, vercel_url, created_at, updated_at')
      .eq('user_id', ctx.userId)
      .order('created_at', { ascending: false });

    if (error) {
      console.error('[platform-repos] Query error:', error);
      return NextResponse.json({ repos: [] });
    }

    if (!sessions?.length) {
      return NextResponse.json({ repos: [] });
    }

    // Filter to rows that have AT LEAST one identifier we can use as a
    // workspace key (project_id OR platform_repo_url). Skip pure-empty
    // sessions (idle wizard opens, broken auth handshakes, etc).
    const usable = sessions.filter(
      (s) => s.project_id || s.platform_repo_url
    );

    // Deduplicate by project_id when we have one, otherwise by repo URL.
    // This way a session that pushed to GitHub but never got project_id
    // assigned still surfaces, while normal cases collapse correctly.
    const seen = new Set();
    const uniqueSessions = usable.filter((s) => {
      const key = s.project_id || s.platform_repo_url;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    // Build response — prefer the repo name when the project has been
    // exported, otherwise fall back to the chat session title (which is
    // seeded from the user's first prompt) so drafts have a real label.
    const repos = uniqueSessions.map(s => {
      const repoName = s.platform_repo_url
        ?.replace('https://github.com/', '')
        ?.split('/')
        ?.pop() || null;

      const fallbackTitle = (s.title || '').trim().slice(0, 60) || 'Untitled project';
      const projectName = repoName || fallbackTitle;

      // Always return a projectId — fall back to the chat-session id when
      // the row's project_id column was never populated. The workspace
      // launcher routes to /workspace/<projectId>, so a missing key here
      // would silently break the "Recent Projects" tile click.
      return {
        projectId: s.project_id || s.id,
        repoUrl: s.platform_repo_url || null,
        repoName: repoName || projectName,
        projectName,
        createdAt: s.created_at,
        deployUrl: s.vercel_url || null,
        deployStatus: s.vercel_url ? 'deployed' : null,
      };
    });

    return NextResponse.json({ repos });
  } catch (err) {
    console.error('[platform-repos] Error:', err);
    return NextResponse.json({ repos: [] });
  }
}
