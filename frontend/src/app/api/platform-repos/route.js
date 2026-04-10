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

    // Query chat_sessions with platform_repo_url for this user
    const { data: sessions, error } = await supabase
      .from('chat_sessions')
      .select('id, project_id, platform_repo_url, vercel_url, created_at, updated_at')
      .eq('user_id', ctx.userId)
      .not('platform_repo_url', 'is', null)
      .order('created_at', { ascending: false });

    if (error) {
      console.error('[platform-repos] Query error:', error);
      return NextResponse.json({ repos: [] });
    }

    if (!sessions?.length) {
      return NextResponse.json({ repos: [] });
    }

    // Deduplicate by project_id (keep latest)
    const seen = new Set();
    const uniqueSessions = sessions.filter(s => {
      const key = s.project_id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    // Build response — derive names from the repo URL directly
    const repos = uniqueSessions.map(s => {
      const repoName = s.platform_repo_url
        ?.replace('https://github.com/', '')
        ?.split('/')
        ?.pop() || 'Unknown';

      return {
        projectId: s.project_id,
        repoUrl: s.platform_repo_url,
        repoName,
        projectName: repoName,
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
