import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

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

    // Query chat_sessions. Returns ALL projects the user has access to —
    // both owned and projects they were invited to. RLS on chat_sessions
    // (migration 020) restricts SELECT to project members, so we don't
    // need (and must NOT add) an explicit user_id filter — that would hide
    // shared projects that the user joined via an invite.
    const { data: sessions, error } = await supabase
      .from('chat_sessions')
      .select('id, project_id, title, platform_repo_url, vercel_url, created_at, updated_at')
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

    // Score a session row so we can pick the richest one per project. Shared
    // projects accumulate multiple chat_sessions rows under the same slug —
    // one per user who ever opened the workspace. The original owner's row
    // carries the real title/repo/deploy URL; placeholder rows created when
    // invited members open the workspace have title="New workspace session"
    // and no repo/deploy URL. Higher score wins. (We still keep created_at
    // DESC as a tiebreaker via the iteration order of `sessions`.)
    const scoreSession = (s) => {
      let score = 0;
      if (s.platform_repo_url) score += 100;
      if (s.vercel_url) score += 50;
      const title = (s.title || '').trim();
      if (title && title !== 'New workspace session') score += 10;
      return score;
    };

    // Deduplicate by project_id when we have one, otherwise by repo URL.
    // For each key, keep the row with the highest score so shared projects
    // display the owner's title rather than an editor's placeholder row.
    const byKey = new Map();
    for (const s of usable) {
      const key = s.project_id || s.platform_repo_url;
      const prev = byKey.get(key);
      if (!prev || scoreSession(s) > scoreSession(prev)) {
        byKey.set(key, s);
      }
    }
    const uniqueSessions = [...byKey.values()].sort(
      (a, b) => new Date(b.created_at) - new Date(a.created_at)
    );

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
