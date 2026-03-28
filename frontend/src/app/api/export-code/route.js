import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';
import { generateDockerfile } from '@/lib/templates/dockerfile';
import { generateNginxConf } from '@/lib/templates/nginx';
import { generateGitlabCI } from '@/lib/templates/cicd';
import { generateMakefile } from '@/lib/templates/makefile';

// ─────────────────────────────────────────────────────────
//  POST /api/export-code
//
//  Clones the platform project's generated code and pushes it
//  to a NEW repo on the user's connected provider (GitHub,
//  GitLab, or Bitbucket) without touching the platform copy.
//
//  Body: { projectId, provider, repoName, isPrivate }
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

  const { projectId, provider, repoName, isPrivate = true, includeCICD = false } = body;

  if (!projectId || !provider || !repoName?.trim()) {
    return NextResponse.json(
      { error: 'projectId, provider, and repoName are required' },
      { status: 400 }
    );
  }

  if (!['github', 'gitlab', 'bitbucket'].includes(provider)) {
    return NextResponse.json({ error: 'Invalid provider' }, { status: 400 });
  }

  // ── Get user's integration token ───────────────────
  // ctx.user comes from requireAuth() → supabase.auth.getUser()
  // and already contains user_metadata with integration tokens.
  const user = ctx.user;
  if (!user) {
    return NextResponse.json({ error: 'User not found' }, { status: 404 });
  }

  const meta = user.user_metadata || {};
  const integration = meta[`${provider}_integration`];

  if (!integration?.connected || !integration?.token) {
    return NextResponse.json(
      { error: `${provider} is not connected. Please connect first.` },
      { status: 400 }
    );
  }
  // ── Get project files from deployment record ───────
  const supabase = await getSupabaseServerClient();
  const { data: deployment } = await supabase
    .from('project_deployments')
    .select('repo_url, gitlab_project_id')
    .eq('user_id', ctx.userId)
    .eq('project_id', projectId)
    .maybeSingle();

  // ── Check if this project has a platform GitHub repo ──
  let platformRepoUrl = '';
  try {
    const { data: chatSession } = await supabase
      .from('chat_sessions')
      .select('platform_repo_url')
      .eq('user_id', ctx.userId)
      .eq('project_id', projectId)
      .not('platform_repo_url', 'is', null)
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();
    platformRepoUrl = chatSession?.platform_repo_url || '';
  } catch (e) {
    console.warn('[export-code] Could not check platform_repo_url:', e);
  }

  // ── Fetch files — priority: platform GitHub → platform GitLab → workspace ──
  let files = [];

  // 1. Try platform GitHub repo (wizard-created projects)
  if (files.length === 0 && platformRepoUrl) {
    try {
      files = await fetchPlatformGitHubFiles(platformRepoUrl);
    } catch (err) {
      console.error('[export-code] Failed to fetch from platform GitHub:', err);
    }
  }

  // 2. Try platform GitLab (legacy deployment pipeline)
  if (files.length === 0) {
    try {
      files = await fetchPlatformRepoFiles(deployment, ctx.userId);
    } catch (err) {
      console.error('[export-code] Failed to fetch platform GitLab files:', err);
    }
  }

  // 3. Fallback: fetch from agent workspace (scratch sessions)
  if (files.length === 0) {
    try {
      files = await fetchWorkspaceFiles(ctx.userId, ctx.accessToken, projectId);
    } catch (err) {
      console.error('[export-code] Failed to fetch workspace files:', err);
    }
  }

  if (files.length === 0) {
    return NextResponse.json(
      { error: 'No files found in the project. Make sure the agent has generated code first.' },
      { status: 404 }
    );
  }

  // ── If CI/CD requested, generate and inject infra files ─
  if (includeCICD) {
    const cleanSlug = repoName.trim().toLowerCase().replace(/[^a-z0-9-_.]/g, '-');
    const isAdmin = /admin|dashboard|panel|cms/i.test(cleanSlug);

    // Determine stack from existing files
    const hasNextConfig = files.some(f => /next\.config/i.test(f.path));
    const hasPackageJson = files.some(f => f.path === 'package.json');
    const stack = hasNextConfig ? 'nextjs' : hasPackageJson ? 'react' : 'html-css';

    const docker = generateDockerfile({ stack });
    const cicdFiles = [
      { path: 'Dockerfile', content: docker.content },
      { path: 'nginx.conf', content: generateNginxConf({ isAdmin }) },
      { path: 'Makefile', content: generateMakefile({ projectSlug: cleanSlug }) },
    ];

    // Only add .gitlab-ci.yml for GitLab exports
    if (provider === 'gitlab') {
      cicdFiles.push({ path: '.gitlab-ci.yml', content: generateGitlabCI() });
    }

    // Add CI/CD files (don't overwrite existing files)
    const existingPaths = new Set(files.map(f => f.path));
    for (const ciFile of cicdFiles) {
      if (!existingPaths.has(ciFile.path)) {
        files.push(ciFile);
      }
    }

    console.log(`[export-code] CI/CD files added: ${cicdFiles.filter(f => !existingPaths.has(f.path)).map(f => f.path).join(', ')}`);
  }

  // ── Create repo + push on user's provider ──────────
  try {
    let repoUrl;
    const cleanName = repoName.trim().toLowerCase().replace(/[^a-z0-9-_.]/g, '-');

    if (provider === 'github') {
      repoUrl = await exportToGitHub({
        token: integration.token,
        repoName: cleanName,
        isPrivate,
        files,
      });
    } else if (provider === 'gitlab') {
      repoUrl = await exportToGitLab({
        token: integration.token,
        host: integration.host || 'https://gitlab.com',
        repoName: cleanName,
        isPrivate,
        files,
      });
    } else if (provider === 'bitbucket') {
      repoUrl = await exportToBitbucket({
        username: integration.username,
        appPassword: integration.token,
        repoName: cleanName,
        isPrivate,
        files,
      });
    }

    // Save export record
    await supabase.from('project_exports').upsert({
      user_id: ctx.userId,
      project_id: projectId,
      provider,
      repo_url: repoUrl,
      repo_name: cleanName,
      exported_at: new Date().toISOString(),
    }, { onConflict: 'user_id,project_id,provider' }).catch(() => {});

    // Save user repo URL to chat_sessions so workspace UI shows it
    try {
      await supabase.from('chat_sessions')
        .update({ user_repo_url: repoUrl, user_repo_provider: provider })
        .eq('project_id', projectId)
        .eq('user_id', ctx.userId);
    } catch (e) {
      console.warn('[export-code] Could not save user_repo_url:', e);
    }

    return NextResponse.json({
      ok: true,
      repoUrl,
      provider,
    });
  } catch (err) {
    console.error('[export-code] Export failed:', err);
    return NextResponse.json(
      { error: err.message || 'Export failed' },
      { status: 500 }
    );
  }
}

// ═══════════════════════════════════════════════════════════
//  Fetch files from the platform's GitHub repo (wizard projects)
//  Uses PLATFORM_GITHUB_TOKEN to read from the platform-owned repo.
// ═══════════════════════════════════════════════════════════

async function fetchPlatformGitHubFiles(platformRepoUrl) {
  const token = process.env.PLATFORM_GITHUB_TOKEN || '';
  if (!token || !platformRepoUrl) return [];

  // Parse "https://github.com/Owner/repo-name" → Owner/repo-name
  const repoPath = platformRepoUrl
    .replace('https://github.com/', '')
    .replace(/\.git$/, '')
    .replace(/\/$/, '');

  if (!repoPath || !repoPath.includes('/')) return [];

  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
  };

  // 1. Get the full file tree recursively
  const treeRes = await fetch(
    `https://api.github.com/repos/${repoPath}/git/trees/main?recursive=1`,
    { headers, signal: AbortSignal.timeout(15000) }
  );

  if (!treeRes.ok) {
    // Try "master" branch as fallback
    const treeRes2 = await fetch(
      `https://api.github.com/repos/${repoPath}/git/trees/master?recursive=1`,
      { headers, signal: AbortSignal.timeout(15000) }
    );
    if (!treeRes2.ok) throw new Error(`GitHub tree fetch failed: ${treeRes.status}`);
    const treeData2 = await treeRes2.json();
    return await fetchGitHubBlobContents(repoPath, treeData2.tree || [], headers);
  }

  const treeData = await treeRes.json();
  return await fetchGitHubBlobContents(repoPath, treeData.tree || [], headers);
}

async function fetchGitHubBlobContents(repoPath, tree, headers) {
  // Filter to blobs (files) only, skip huge files
  const blobs = tree.filter(
    (t) => t.type === 'blob' && (t.size || 0) < 500_000
  );

  const BATCH_SIZE = 10;
  const files = [];

  for (let i = 0; i < blobs.length; i += BATCH_SIZE) {
    const batch = blobs.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (blob) => {
        try {
          const res = await fetch(
            `https://api.github.com/repos/${repoPath}/contents/${blob.path.split('/').map(s => encodeURIComponent(s)).join('/')}?ref=main`,
            { headers, signal: AbortSignal.timeout(10000) }
          );
          if (!res.ok) return null;
          const data = await res.json();
          // GitHub returns base64-encoded content
          const content = data.encoding === 'base64'
            ? Buffer.from(data.content, 'base64').toString('utf-8')
            : data.content || '';
          return { path: blob.path, content };
        } catch {
          return null;
        }
      })
    );
    files.push(...results.filter(Boolean));
  }

  console.log(`[export-code] Fetched ${files.length} files from platform GitHub`);
  return files;
}

// ═══════════════════════════════════════════════════════════
//  Fetch files from the platform GitLab repo
// ═══════════════════════════════════════════════════════════

async function fetchPlatformRepoFiles(deployment, userId) {
  // If we have the GitLab project ID (from our deployment pipeline),
  // use the platform GitLab to fetch the file tree
  const gitlabToken = process.env.SUPABASE_ACCESS_TOKEN || process.env.GITLAB_TOKEN || '';
  const gitlabHost = process.env.GITLAB_HOST || 'https://gitlab.udevs.io';

  if (deployment?.gitlab_project_id && gitlabToken) {
    const api = `${gitlabHost}/api/v4`;

    // Get tree
    const treeRes = await fetch(
      `${api}/projects/${deployment.gitlab_project_id}/repository/tree?recursive=true&per_page=100&ref=main`,
      { headers: { 'PRIVATE-TOKEN': gitlabToken }, signal: AbortSignal.timeout(15000) }
    );
    if (!treeRes.ok) throw new Error('Failed to fetch repo tree');
    const tree = await treeRes.json();

    const blobs = tree.filter(t => t.type === 'blob');

    // Fetch file contents in batches (max 10 parallel)
    const BATCH_SIZE = 10;
    const files = [];

    for (let i = 0; i < blobs.length; i += BATCH_SIZE) {
      const batch = blobs.slice(i, i + BATCH_SIZE);
      const results = await Promise.all(
        batch.map(async (blob) => {
          const fileRes = await fetch(
            `${api}/projects/${deployment.gitlab_project_id}/repository/files/${encodeURIComponent(blob.path)}/raw?ref=main`,
            { headers: { 'PRIVATE-TOKEN': gitlabToken }, signal: AbortSignal.timeout(10000) }
          );
          if (!fileRes.ok) return null;
          const content = await fileRes.text();
          return { path: blob.path, content };
        })
      );
      files.push(...results.filter(Boolean));
    }

    return files;
  }

  return [];
}

// ═══════════════════════════════════════════════════════════
//  Fetch files from the agent workspace (scratch sessions)
// ═══════════════════════════════════════════════════════════

async function fetchWorkspaceFiles(userId, accessToken, projectId) {
  const AI_SERVICE_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';

  // First, find active sessions for this user+project via the sessions endpoint
  // The workspace files are accessible via session_id.
  // We need to find the session that matches this projectId.
  try {
    // Try to find session by querying chat_sessions for this project
    const { getSupabaseServerClient } = await import('@/lib/supabase/server');
    const supabase = await getSupabaseServerClient();

    const { data: chatSessions } = await supabase
      .from('chat_sessions')
      .select('agent_session_id')
      .eq('user_id', userId)
      .eq('project_id', projectId)
      .order('created_at', { ascending: false })
      .limit(1);

    if (!chatSessions?.length || !chatSessions[0].agent_session_id) {
      console.log('[export-code] No agent session found for project:', projectId);
      return [];
    }

    const sessionId = chatSessions[0].agent_session_id;

    // Build headers for auth
    const headers = {};
    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    } else {
      headers['X-User-ID'] = userId;
      const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';
      if (INTERNAL_API_KEY) headers['X-Internal-Key'] = INTERNAL_API_KEY;
    }

    const res = await fetch(
      `${AI_SERVICE_URL}/api/v1/files/export?session_id=${encodeURIComponent(sessionId)}`,
      { headers, signal: AbortSignal.timeout(30000) }
    );

    if (!res.ok) {
      console.error('[export-code] Workspace export failed:', res.status, await res.text());
      return [];
    }

    const data = await res.json();
    console.log(`[export-code] Fetched ${data.count} files from workspace`);
    return data.files || [];
  } catch (err) {
    console.error('[export-code] fetchWorkspaceFiles error:', err);
    return [];
  }
}

// ═══════════════════════════════════════════════════════════
//  GitHub Export
// ═══════════════════════════════════════════════════════════

async function exportToGitHub({ token, repoName, isPrivate, files }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
  };

  // 1. Create repo
  const createRes = await fetch('https://api.github.com/user/repos', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: repoName,
      private: isPrivate,
      auto_init: true,
      description: 'Exported from Lucid AI',
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!createRes.ok) {
    const err = await createRes.json();
    throw new Error(err.message || 'Failed to create GitHub repo');
  }

  const repo = await createRes.json();
  const fullName = repo.full_name;

  // Wait for init to propagate
  await sleep(2000);

  // 2. Push files using the Git Trees/Commits API for atomic pushes
  // Get the default branch ref
  const refRes = await fetch(`https://api.github.com/repos/${fullName}/git/ref/heads/main`, {
    headers,
    signal: AbortSignal.timeout(10000),
  });

  if (!refRes.ok) {
    // Maybe default branch is "master"
    const refRes2 = await fetch(`https://api.github.com/repos/${fullName}/git/ref/heads/master`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!refRes2.ok) throw new Error('Could not get default branch ref');
  }

  const ref = await (refRes.ok ? refRes : refRes).json();
  const baseSha = ref.object.sha;

  // 3. Create blobs for each file
  const blobPromises = files.map(async (f) => {
    const blobRes = await fetch(`https://api.github.com/repos/${fullName}/git/blobs`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        content: f.content,
        encoding: 'utf-8',
      }),
      signal: AbortSignal.timeout(10000),
    });
    if (!blobRes.ok) return null;
    const blob = await blobRes.json();
    return { path: f.path, sha: blob.sha, mode: '100644', type: 'blob' };
  });

  const treeItems = (await Promise.all(blobPromises)).filter(Boolean);

  // 4. Create tree
  const treeRes = await fetch(`https://api.github.com/repos/${fullName}/git/trees`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      base_tree: baseSha,
      tree: treeItems,
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!treeRes.ok) throw new Error('Failed to create git tree');
  const tree = await treeRes.json();

  // 5. Create commit
  const commitRes = await fetch(`https://api.github.com/repos/${fullName}/git/commits`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      message: '🚀 Exported from Lucid AI',
      tree: tree.sha,
      parents: [baseSha],
    }),
    signal: AbortSignal.timeout(10000),
  });

  if (!commitRes.ok) throw new Error('Failed to create commit');
  const commit = await commitRes.json();

  // 6. Update ref
  await fetch(`https://api.github.com/repos/${fullName}/git/refs/heads/main`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ sha: commit.sha, force: true }),
    signal: AbortSignal.timeout(10000),
  });

  return repo.html_url;
}

// ═══════════════════════════════════════════════════════════
//  GitLab Export
// ═══════════════════════════════════════════════════════════

async function exportToGitLab({ token, host, repoName, isPrivate, files }) {
  const api = `${host.replace(/\/+$/, '')}/api/v4`;
  const headers = {
    'Content-Type': 'application/json',
    'PRIVATE-TOKEN': token,
  };

  // 1. Create project
  const createRes = await fetch(`${api}/projects`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: repoName,
      visibility: isPrivate ? 'private' : 'public',
      initialize_with_readme: false,
      description: 'Exported from Lucid AI',
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!createRes.ok) {
    const err = await createRes.text();
    throw new Error(`GitLab repo creation failed: ${err}`);
  }

  const project = await createRes.json();

  // 2. Push all files in a single commit
  const actions = files.map((f) => ({
    action: 'create',
    file_path: f.path,
    content: f.content,
  }));

  const commitRes = await fetch(`${api}/projects/${project.id}/repository/commits`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      branch: 'main',
      start_branch: 'main',
      commit_message: '🚀 Exported from Lucid AI',
      actions,
    }),
    signal: AbortSignal.timeout(30000),
  });

  if (!commitRes.ok) {
    const err = await commitRes.text();
    throw new Error(`GitLab push failed: ${err}`);
  }

  return project.web_url;
}

// ═══════════════════════════════════════════════════════════
//  Bitbucket Export
// ═══════════════════════════════════════════════════════════

async function exportToBitbucket({ username, appPassword, repoName, isPrivate, files }) {
  const authHeader = `Basic ${Buffer.from(`${username}:${appPassword}`).toString('base64')}`;

  // 1. Create repo
  const createRes = await fetch(
    `https://api.bitbucket.org/2.0/repositories/${username}/${repoName}`,
    {
      method: 'POST',
      headers: {
        Authorization: authHeader,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        scm: 'git',
        is_private: isPrivate,
        description: 'Exported from Lucid AI',
        mainbranch: { type: 'branch', name: 'main' },
      }),
      signal: AbortSignal.timeout(15000),
    }
  );

  if (!createRes.ok) {
    const err = await createRes.text();
    throw new Error(`Bitbucket repo creation failed: ${err}`);
  }

  const repo = await createRes.json();

  // 2. Push files using the src endpoint (multi-file upload via form-data)
  // Bitbucket supports pushing via the "src" endpoint with multipart form
  const boundary = `----LucidExport${Date.now()}`;
  let formBody = '';

  for (const f of files) {
    formBody += `--${boundary}\r\n`;
    formBody += `Content-Disposition: form-data; name="${f.path}"; filename="${f.path}"\r\n`;
    formBody += `Content-Type: application/octet-stream\r\n\r\n`;
    formBody += `${f.content}\r\n`;
  }

  // Add commit message
  formBody += `--${boundary}\r\n`;
  formBody += `Content-Disposition: form-data; name="message"\r\n\r\n`;
  formBody += `🚀 Exported from Lucid AI\r\n`;
  formBody += `--${boundary}\r\n`;
  formBody += `Content-Disposition: form-data; name="branch"\r\n\r\n`;
  formBody += `main\r\n`;
  formBody += `--${boundary}--\r\n`;

  const pushRes = await fetch(
    `https://api.bitbucket.org/2.0/repositories/${username}/${repoName}/src`,
    {
      method: 'POST',
      headers: {
        Authorization: authHeader,
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
      },
      body: formBody,
      signal: AbortSignal.timeout(30000),
    }
  );

  if (!pushRes.ok && pushRes.status !== 201) {
    const err = await pushRes.text();
    throw new Error(`Bitbucket push failed: ${err}`);
  }

  return repo.links?.html?.href || `https://bitbucket.org/${username}/${repoName}`;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
