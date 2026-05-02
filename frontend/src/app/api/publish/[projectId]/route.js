import { NextResponse } from 'next/server';
import { requireAuth, AI_SERVICE_URL } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';
import { canPublishProject } from '@/lib/subscription';

// ─────────────────────────────────────────────────────────
//  POST /api/publish/[projectId]
//
//  Publishes a generated project: merges staging→main on the
//  Lucid-owned GitHub repo, updates visibility, kicks off Vercel
//  project creation in the background. Returns the GitHub URL
//  immediately — does NOT wait for the Vercel build to finish.
//
//  Body: { visibility?: 'private' | 'public' }  (default: 'private')
// ─────────────────────────────────────────────────────────

const GH_API = 'https://api.github.com';
const VERCEL_API = 'https://api.vercel.com';

export async function POST(req, { params }) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const { projectId } = await params;
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  let body = {};
  try { body = await req.json(); } catch {}
  const visibility = body.visibility === 'public' ? 'public' : 'private';

  // ── Tier check ─────────────────────────────────────────
  const tierCheck = await canPublishProject(ctx.userId, projectId);
  if (!tierCheck.allowed) {
    return NextResponse.json(
      { error: tierCheck.reason, upgradeRequired: true, plan: tierCheck.plan },
      { status: 403 }
    );
  }

  // ── Load project repo info ─────────────────────────────
  const supabase = await getSupabaseServerClient();
  const { data: session, error: sessErr } = await supabase
    .from('chat_sessions')
    .select('platform_repo_url, platform_repo_branch, vercel_url')
    .eq('user_id', ctx.userId)
    .eq('project_id', projectId)
    .maybeSingle();

  if (sessErr) {
    return NextResponse.json(
      { error: 'Could not load project info — please try again.' },
      { status: 500 }
    );
  }

  // ── Imported project (no platform repo yet) → bootstrap via ai_engine ──
  // The ai_engine service has the workspace files mounted; the frontend does
  // not. So first-time publish for imported projects must run server-side
  // in Python where it can read /app/storage/{user}/{project}.
  if (!session?.platform_repo_url) {
    try {
      // 5-minute timeout — bootstrap-publish does git clone + git push +
      // Vercel API calls; first-publish for an imported repo can take 60-90s.
      // The browser's default fetch has no timeout, but Node's undici sets
      // ~5min by default — make it explicit so we control the failure mode.
      const res = await fetch(
        `${AI_SERVICE_URL}/api/v1/projects/${projectId}/bootstrap-publish`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${ctx.accessToken}`,
          },
          body: JSON.stringify({ visibility }),
          signal: AbortSignal.timeout(300000),
        }
      );
      const data = await res.json();
      if (!res.ok) {
        return NextResponse.json(
          { error: data.detail || data.error || 'Could not publish your project.' },
          { status: res.status }
        );
      }
      return NextResponse.json({
        ok: true,
        repoUrl: data.repoUrl,
        vercelUrl: data.vercelUrl,
        visibility: data.visibility || visibility,
        message: data.message,
      });
    } catch (err) {
      console.error('[publish] bootstrap proxy failed:', err);
      const isTimeout = err?.name === 'TimeoutError' || /abort/i.test(String(err?.message || ''));
      return NextResponse.json(
        {
          error: isTimeout
            ? 'Publishing is taking longer than expected. Your code is probably already saved — wait a minute and click Publish again.'
            : 'Could not reach the publish service — please try again.',
        },
        { status: isTimeout ? 504 : 502 }
      );
    }
  }

  const platformToken = process.env.PLATFORM_GITHUB_TOKEN;
  if (!platformToken) {
    return NextResponse.json(
      { error: 'PLATFORM_GITHUB_TOKEN not configured on server' },
      { status: 500 }
    );
  }

  // ── Sync the live workspace into staging FIRST ──────────────────────────
  // Without this step, "publish" only fast-forwards main → staging SHA, so any
  // edits in the live preview workspace (e.g. our next.config.mjs assetPrefix
  // hot-fix, or anything the user changed via the chat agent without
  // committing) never reach Vercel. We call the ai_engine sync-workspace
  // endpoint which runs `git add -A && git commit && git push origin staging`
  // against the live workspace. Best-effort — if it fails (no workspace yet,
  // nothing changed, network blip), the SHA promotion below still runs so
  // the existing publish flow keeps working.
  let syncResult = { changed: false, filesPushed: 0 };
  try {
    const syncRes = await fetch(
      `${AI_SERVICE_URL}/api/v1/projects/${projectId}/sync-workspace`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${ctx.accessToken}`,
        },
        signal: AbortSignal.timeout(240000),
      }
    );
    if (syncRes.ok) {
      const syncData = await syncRes.json().catch(() => ({}));
      syncResult = {
        changed: !!syncData.changed,
        filesPushed: syncData.filesPushed || 0,
      };
      if (syncResult.changed) {
        console.log(`[publish] synced ${syncResult.filesPushed} workspace file(s) to staging`);
      }
    } else {
      // 404 (no workspace yet) and similar are expected in some cases — don't
      // surface as a publish failure, just log so the SHA promotion still runs.
      const errTxt = await syncRes.text().catch(() => '');
      console.warn(`[publish] sync-workspace skipped (${syncRes.status}): ${errTxt.slice(0, 200)}`);
    }
  } catch (syncErr) {
    console.warn(`[publish] sync-workspace request failed (non-fatal): ${syncErr?.message || syncErr}`);
  }

  // Parse owner/repo from html URL — e.g. "https://github.com/LucidSoftware-tech/foo-frontend"
  const match = session.platform_repo_url.match(/github\.com\/([^/]+)\/([^/]+)/);
  if (!match) {
    return NextResponse.json(
      { error: 'Could not parse owner/repo from platform_repo_url' },
      { status: 500 }
    );
  }
  const owner = match[1];
  const repo = match[2].replace(/\.git$/, '');
  const sourceBranch = session.platform_repo_branch || 'staging';

  const ghHeaders = {
    Authorization: `Bearer ${platformToken}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
  };

  try {
    // ── 1. Update repo visibility ────────────────────────
    const visRes = await fetch(`${GH_API}/repos/${owner}/${repo}`, {
      method: 'PATCH',
      headers: ghHeaders,
      body: JSON.stringify({ private: visibility === 'private' }),
      signal: AbortSignal.timeout(30000),
    });
    if (!visRes.ok && visRes.status !== 422) {
      const txt = await visRes.text().catch(() => '');
      console.warn('[publish] visibility update failed:', visRes.status, txt.slice(0, 200));
    }

    // ── 2. Get staging branch SHA ────────────────────────
    const refRes = await fetch(
      `${GH_API}/repos/${owner}/${repo}/git/ref/heads/${sourceBranch}`,
      { headers: ghHeaders, signal: AbortSignal.timeout(10000) }
    );
    if (!refRes.ok) {
      const txt = await refRes.text().catch(() => '');
      throw new Error(`Could not read ${sourceBranch} branch: ${refRes.status} ${txt.slice(0, 150)}`);
    }
    const stagingSha = (await refRes.json()).object.sha;

    // ── 3. Push to main (create or fast-forward) ─────────
    const mainRefRes = await fetch(
      `${GH_API}/repos/${owner}/${repo}/git/ref/heads/main`,
      { headers: ghHeaders, signal: AbortSignal.timeout(10000) }
    );

    // Helper — POST a fresh ref. Used both for "main doesn't exist yet"
    // AND as a fallback when PATCH unexpectedly 404s (GitHub API has
    // intermittent windows where GET succeeds but PATCH on the same ref
    // returns 404; observed in production).
    const createMain = async () => {
      const createRes = await fetch(`${GH_API}/repos/${owner}/${repo}/git/refs`, {
        method: 'POST',
        headers: ghHeaders,
        body: JSON.stringify({ ref: 'refs/heads/main', sha: stagingSha }),
        signal: AbortSignal.timeout(30000),
      });
      // 422 "already exists" is success-equivalent — main is there with the
      // SHA we wanted. Other 4xx/5xx is a real failure.
      if (!createRes.ok && createRes.status !== 422) {
        const txt = await createRes.text().catch(() => '');
        // Include owner/repo/sha in the error so we can triage 404s without
        // having to repro — bare "404 Not Found" hides which side broke.
        throw new Error(
          `Failed to create main on ${owner}/${repo} from ${sourceBranch}@${stagingSha.slice(0, 8)}: ` +
          `${createRes.status} ${txt.slice(0, 150)}`
        );
      }
    };

    if (mainRefRes.status === 404) {
      await createMain();
    } else if (mainRefRes.ok) {
      // main exists — try fast-forward (force update to staging's SHA).
      // If PATCH 404s anyway (GitHub edge case), recover by re-creating.
      const updateRes = await fetch(
        `${GH_API}/repos/${owner}/${repo}/git/refs/heads/main`,
        {
          method: 'PATCH',
          headers: ghHeaders,
          body: JSON.stringify({ sha: stagingSha, force: true }),
          signal: AbortSignal.timeout(30000),
        }
      );
      if (updateRes.status === 404) {
        console.warn('[publish] PATCH main 404 — falling back to POST create');
        await createMain();
      } else if (!updateRes.ok) {
        const txt = await updateRes.text().catch(() => '');
        throw new Error(`Failed to update main: ${updateRes.status} ${txt.slice(0, 150)}`);
      }
    } else {
      const txt = await mainRefRes.text().catch(() => '');
      throw new Error(`Unexpected GitHub error on main check: ${mainRefRes.status} ${txt.slice(0, 150)}`);
    }

    // ── 4. Fire-and-forget Vercel project ensure + deploy ───────
    // ALWAYS call this — first-publish creates the project and deploys,
    // republish just triggers a new deploy on the existing project.
    // Vercel's webhook doesn't fire reliably for our flow because we push
    // to main *before* the project link, so we trigger deploys explicitly.
    const vercelToken = process.env.VERCEL_TOKEN;
    let resolvedVercelUrl = null;
    if (vercelToken) {
      const teamId = process.env.VERCEL_TEAM_ID || '';
      const projectSlug = repo.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 50);

      // Resolve the actual canonical *.vercel.app URL by fetching the project
      // record and reading targets.production.alias. Vercel truncates hostnames
      // when the slug + team suffix would exceed its DNS limits (~35-char cap),
      // so naively predicting `${slug}.vercel.app` 404s for long repo names.
      resolvedVercelUrl = await resolveVercelProductionUrl({
        token: vercelToken, teamId, projectSlug,
      });

      // Fallback chain: prefer freshly-resolved alias, then whatever was saved
      // on a prior publish, finally a best-guess prediction (last resort, may 404).
      if (!resolvedVercelUrl) {
        resolvedVercelUrl = session.vercel_url || `https://${projectSlug}.vercel.app`;
      }

      ensureVercelProject({
        token: vercelToken,
        teamId,
        projectSlug,
        owner,
        repo,
        ghToken: platformToken,
      }).catch((e) => console.warn('[publish] Vercel ensure+deploy failed (non-fatal):', e.message));
    }

    // ── 5. Save deployment record ────────────────────────
    await supabase
      .from('project_deployments')
      .upsert(
        {
          user_id: ctx.userId,
          project_id: projectId,
          repo_url: session.platform_repo_url,
          deploy_url: resolvedVercelUrl,
          deploy_method: 'vercel',
          status: 'deployed',
          deployed_at: new Date().toISOString(),
        },
        { onConflict: 'user_id,project_id' }
      );

    // Always overwrite the saved URL with the resolved canonical one — earlier
    // publishes may have stored a wrongly-predicted URL that 404s.
    if (resolvedVercelUrl && resolvedVercelUrl !== session.vercel_url) {
      await supabase
        .from('chat_sessions')
        .update({ vercel_url: resolvedVercelUrl })
        .eq('user_id', ctx.userId)
        .eq('project_id', projectId);
    }

    return NextResponse.json({
      ok: true,
      repoUrl: session.platform_repo_url,
      vercelUrl: resolvedVercelUrl,
      visibility,
      synced: syncResult.changed,
      filesPushed: syncResult.filesPushed,
      message: syncResult.changed
        ? `Published! ${syncResult.filesPushed} workspace file change${syncResult.filesPushed === 1 ? '' : 's'} pushed and Vercel is rebuilding — check back in a minute.`
        : 'Published! Your code is live on main. Vercel is building in the background — check back in a minute.',
    });
  } catch (err) {
    console.error('[publish] error:', err);
    return NextResponse.json(
      { error: err.message || 'Publish failed' },
      { status: 502 }
    );
  }
}

// ─────────────────────────────────────────────────────────
//  Resolve the canonical production URL Vercel actually serves
//
//  For projects whose slug exceeds Vercel's hostname budget (~35 chars once
//  the team suffix is appended), Vercel truncates the assigned alias. The
//  truncation rules aren't public, so we read the real alias from the project
//  record instead of trying to replicate them. Returns null when the project
//  doesn't exist yet OR has no production alias assigned (fresh project,
//  pre-first-deploy).
// ─────────────────────────────────────────────────────────
async function resolveVercelProductionUrl({ token, teamId, projectSlug }) {
  const qs = teamId ? `?teamId=${teamId}` : '';
  try {
    const res = await fetch(`${VERCEL_API}/v9/projects/${projectSlug}${qs}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    const proj = await res.json();
    const aliases = proj?.targets?.production?.alias || [];
    // Filter to plain *.vercel.app entries (skip git-branch and team-scoped
    // aliases). Pick the shortest — that's Vercel's canonical assignment.
    const canonical = aliases
      .filter((a) => /\.vercel\.app$/.test(a) && !a.includes('-git-') && !a.includes('-projects.'))
      .sort((a, b) => a.length - b.length)[0];
    return canonical ? `https://${canonical}` : null;
  } catch (e) {
    console.warn('[publish] resolveVercelProductionUrl failed:', e.message);
    return null;
  }
}

// ─────────────────────────────────────────────────────────
//  Background helper — create Vercel project (if missing) + trigger deploy
//
//  Vercel's GitHub webhook only fires on pushes that happen *after* a
//  project is linked. We push to main first, then call this — so we have
//  to trigger the deploy ourselves. Idempotent: republish callers can
//  invoke this every time and Vercel handles the queueing.
// ─────────────────────────────────────────────────────────
async function ensureVercelProject({ token, teamId, projectSlug, owner, repo, ghToken }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
  const qs = teamId ? `?teamId=${teamId}` : '';

  // Resolve project_id + repoId (create the project if missing)
  let projectId;
  let repoId;
  const existsRes = await fetch(`${VERCEL_API}/v9/projects/${projectSlug}${qs}`, {
    headers,
    signal: AbortSignal.timeout(30000),
  });
  if (existsRes.ok) {
    const proj = await existsRes.json();
    projectId = proj.id;
    repoId = proj.link?.repoId;
  } else {
    const createRes = await fetch(`${VERCEL_API}/v10/projects${qs}`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        name: projectSlug,
        framework: 'nextjs',
        gitRepository: { type: 'github', repo: `${owner}/${repo}` },
      }),
      signal: AbortSignal.timeout(30000),
    });
    if (!createRes.ok) {
      const txt = await createRes.text().catch(() => '');
      throw new Error(`Vercel project creation failed: ${createRes.status} ${txt.slice(0, 200)}`);
    }
    const proj = await createRes.json();
    projectId = proj.id;
    repoId = proj.link?.repoId;
  }

  if (!projectId) return;

  // ── Resolve numeric GitHub repo ID ─────────────────────
  // Vercel's POST /v13/deployments requires gitSource.repoId (numeric),
  // not the "owner/name" string. Prefer the id Vercel already has from
  // the project link; fall back to GitHub's REST API.
  if (!repoId && ghToken) {
    try {
      const ghRes = await fetch(`https://api.github.com/repos/${owner}/${repo}`, {
        headers: {
          Authorization: `Bearer ${ghToken}`,
          Accept: 'application/vnd.github+json',
        },
        signal: AbortSignal.timeout(30000),
      });
      if (ghRes.ok) {
        repoId = (await ghRes.json()).id;
      }
    } catch (e) {
      console.warn('[publish] GitHub repo lookup failed:', e.message);
    }
  }
  if (!repoId) {
    console.warn('[publish] No repoId available — skipping Vercel deploy trigger');
    return;
  }

  // Trigger production deploy from the latest main commit
  const deployRes = await fetch(`${VERCEL_API}/v13/deployments${qs}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: projectSlug,
      project: projectId,
      target: 'production',
      gitSource: { type: 'github', ref: 'main', repoId },
    }),
    signal: AbortSignal.timeout(30000),
  });
  if (!deployRes.ok) {
    const txt = await deployRes.text().catch(() => '');
    console.warn(`[publish] Vercel deploy trigger failed (${deployRes.status}): ${txt.slice(0, 200)}`);
  } else {
    const dep = await deployRes.json().catch(() => ({}));
    console.log(`[publish] Vercel deploy triggered: ${dep.id || '?'} for ${projectSlug}`);
  }
}
