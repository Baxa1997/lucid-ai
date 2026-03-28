// ─────────────────────────────────────────────────────────
//  Lucid AI — Deployment Orchestrator
//
//  Full pipeline:
//    1. Create GitLab repo → push generated code + templates
//    2. Create ops folder in ops repo
//    3. Set CI/CD variables
//    4. Deploy via Vercel (Next.js) or log for manual deploy
//    5. Poll deployment → save live URL
// ─────────────────────────────────────────────────────────

import { createGitLabClient } from '@/lib/gitlab';
import { generateDockerfile } from '@/lib/templates/dockerfile';
import { generateMakefile } from '@/lib/templates/makefile';
import { generateGitlabCI } from '@/lib/templates/cicd';
import { generateOpsFolder } from '@/lib/templates/ops';
import { getSupabaseServerClient } from '@/lib/supabase/server';

const VERCEL_API = 'https://api.vercel.com';

// ═══════════════════════════════════════════════════════════
//  Main deploy function
// ═══════════════════════════════════════════════════════════

/**
 * Deploy a generated project end-to-end.
 *
 * @param {object} opts
 * @param {string} opts.userId — platform user ID
 * @param {string} opts.projectSlug — slug for repo + folder name
 * @param {string} opts.stack — 'nextjs'|'react'|'vue'|'angular'
 * @param {{ path: string, content: string }[]} opts.generatedFiles — from code gen
 * @param {object} opts.settings — user's deployment settings
 * @param {string} opts.platformProjectId — for DB record
 * @returns {{ ok, repoUrl, deployUrl, opsCreated, method, error? }}
 */
export async function deployProject({
  userId,
  projectSlug,
  stack = 'nextjs',
  generatedFiles = [],
  settings = {},
  platformProjectId,
}) {
  const {
    gitlab_host = '',
    gitlab_group = '',
    gitlab_token = '',
    github_token = '',
    ops_repo_url = '',
    ops_repo_branch = 'main',
    vercel_token = '',
    vercel_team_id = '',
    k8s_namespace = 'frontend-prod',
    k8s_domain = '*.udevs.io',
    k8s_tls_secret = '',
    registry_url = 'registry.gitlab.udevs.io',
  } = settings;

  const result = {
    ok: false,
    repoUrl: null,
    deployUrl: null,
    opsCreated: false,
    method: null,
    gitlabProjectId: null,
    provider: null,
  };

  // Determine provider: prefer GitHub if token given, else GitLab
  const useGitHub = !!github_token;
  const useGitLab = !useGitHub && !!gitlab_token && !!gitlab_host;

  // github_token = platform owner's PLATFORM_GITHUB_TOKEN (env var)
  // gitlab_token = platform owner's GitLab token (from user_settings)
  // Users export to their own GitHub/GitLab later via the Export button.

  // Reject fine-grained tokens — they cannot create repos
  if (github_token && github_token.startsWith('github_pat_')) {
    return {
      ...result,
      error: 'Fine-grained GitHub token detected. Use a Classic Personal Access Token (ghp_...) with repo scope. Go to GitHub Settings → Developer Settings → Tokens (classic) → Generate new token.',
    };
  }

  if (!useGitHub && !useGitLab) {
    return { ...result, error: 'No platform GitHub or GitLab token configured. Set PLATFORM_GITHUB_TOKEN env var or configure GitLab in Settings.' };
  }

  // ── Step 1: Create repo (GitHub OR GitLab) ─────────
  let project; // For GitLab — { id, web_url, path }
  let githubFullName; // For GitHub — "owner/repo"

  if (useGitHub) {
    // ── GitHub path ──
    try {
      // Add timestamp suffix to prevent name collisions at scale
      const ts = Date.now().toString(36).slice(-4);
      const repoName = `${userId.substring(0, 8)}-${projectSlug}-${ts}`;
      console.log(`[deploy] Creating GitHub repo: ${repoName}`);

      let ghResult;
      try {
        ghResult = await createGitHubRepo({
          token: github_token,
          repoName,
          isPrivate: true,
        });
      } catch (firstErr) {
        // If name collision (422), retry with random suffix
        if (firstErr.message?.includes('422') || firstErr.message?.includes('already exists')) {
          const fallbackName = `${projectSlug}-${Date.now().toString(36)}`;
          console.log(`[deploy] Name collision, retrying with: ${fallbackName}`);
          ghResult = await createGitHubRepo({
            token: github_token,
            repoName: fallbackName,
            isPrivate: true,
          });
        } else {
          throw firstErr;
        }
      }

      result.repoUrl = ghResult.htmlUrl;
      result.provider = 'github';
      githubFullName = ghResult.fullName;
      console.log(`[deploy] ✓ GitHub repo created: ${ghResult.htmlUrl}`);
    } catch (err) {
      console.error('[deploy] GitHub repo creation failed:', err.message);
      return { ...result, error: `GitHub repo creation failed: ${err.message}` };
    }
  } else {
    // ── GitLab path (existing logic) ──
    try {
      const gitlab = createGitLabClient({ host: gitlab_host, token: gitlab_token });
      const repoName = `${userId.substring(0, 8)}-${projectSlug}`;
      console.log(`[deploy] Creating GitLab repo: ${repoName}`);

      project = await gitlab.createProject({
        name: repoName,
        namespaceId: gitlab_group,
        visibility: 'private',
      });

      result.repoUrl = project.web_url;
      result.gitlabProjectId = project.id;
      result.provider = 'gitlab';
      console.log(`[deploy] ✓ GitLab repo created: ${project.web_url}`);
    } catch (err) {
      console.error('[deploy] GitLab repo creation failed:', err.message);
      return { ...result, error: `Repo creation failed: ${err.message}` };
    }
  }

  // ── Step 2: Generate infrastructure files ──────────
  const docker = generateDockerfile({ stack });
  const allFiles = [...generatedFiles];

  if (useGitLab) {
    // GitLab-specific infra files
    const makefile = generateMakefile({
      projectSlug,
      stack,
      registryUrl: registry_url,
      namespace: k8s_namespace,
    });
    const cicdYml = generateGitlabCI({
      projectSlug,
      stack,
      registryUrl: registry_url,
      namespace: k8s_namespace,
    });
    allFiles.push(
      { path: 'Dockerfile', content: docker.content },
      { path: 'Makefile', content: makefile },
      { path: '.gitlab-ci.yml', content: cicdYml },
    );
  } else {
    // GitHub — just Dockerfile (no CI/CD, Vercel handles deploys)
    allFiles.push({ path: 'Dockerfile', content: docker.content });
  }

  allFiles.push({ path: '.gitignore', content: generateGitignore(stack) });

  // ── Step 3: Push all files to repo ─────────────────
  if (useGitHub) {
    try {
      await pushToGitHub({
        token: github_token,
        fullName: githubFullName,
        files: allFiles,
        commitMessage: '🚀 Initial commit — generated by Lucid AI',
      });
      console.log(`[deploy] ✓ ${allFiles.length} files pushed to GitHub`);
    } catch (err) {
      console.error('[deploy] GitHub push failed:', err.message);
      return { ...result, error: `GitHub push failed: ${err.message}` };
    }
  } else {
    try {
      const gitlab = createGitLabClient({ host: gitlab_host, token: gitlab_token });
      await gitlab.pushFiles({
        projectId: project.id,
        branch: 'main',
        commitMessage: '🚀 Initial commit — generated by Lucid AI',
        files: allFiles,
      });
      console.log(`[deploy] ✓ ${allFiles.length} files pushed to GitLab`);
    } catch (err) {
      console.error('[deploy] File push failed:', err.message);
      return { ...result, error: `File push failed: ${err.message}` };
    }
  }

  // ── Step 4: Set CI/CD variables (GitLab only) ──────
  if (useGitLab && project) {
    try {
      const gitlab = createGitLabClient({ host: gitlab_host, token: gitlab_token });
      await gitlab.setVariable({
        projectId: project.id,
        key: 'K8S_NAMESPACE_PROD',
        value: k8s_namespace,
      });
      console.log('[deploy] ✓ CI/CD variable K8S_NAMESPACE_PROD set');
    } catch (err) {
      console.warn('[deploy] CI/CD variable failed (non-fatal):', err.message);
    }
  }

  // ── Step 5: Create ops folder in ops repo (GitLab only) ──
  if (useGitLab && ops_repo_url && project) {
    try {
      const gitlab = createGitLabClient({ host: gitlab_host, token: gitlab_token });
      const opsRepoPath = extractRepoPath(ops_repo_url);
      const opsProject = await gitlab.findProjectByPath(opsRepoPath);

      if (opsProject) {
        const repoPath = `${gitlab_group ? gitlab_group + '/' : ''}${project.path}`;
        const ops = generateOpsFolder({
          projectSlug,
          repoPath,
          registryUrl: registry_url,
          domain: k8s_domain,
          namespace: k8s_namespace,
          servicePort: docker.expose,
          tlsSecretName: k8s_tls_secret,
        });

        await gitlab.pushFilesToExistingRepo({
          projectId: opsProject.id,
          branch: ops_repo_branch,
          commitMessage: `🔧 Add ops config for ${projectSlug} — Lucid AI`,
          files: ops.files,
        });

        result.opsCreated = true;
        console.log('[deploy] ✓ Ops folder created in', opsRepoPath);
      } else {
        console.warn('[deploy] Ops repo not found:', opsRepoPath);
      }
    } catch (err) {
      console.warn('[deploy] Ops folder creation failed (non-fatal):', err.message);
    }
  }

  // ── Step 6: Deploy via Vercel ──────────────────────
  if (vercel_token) {
    try {
      const deployUrl = await deployToVercel({
        projectSlug,
        repoUrl: result.repoUrl,
        token: vercel_token,
        teamId: vercel_team_id,
        provider: useGitHub ? 'github' : 'gitlab',
      });
      result.deployUrl = deployUrl;
      result.method = 'vercel';
      console.log(`[deploy] ✓ Deployed to Vercel: ${deployUrl}`);
    } catch (err) {
      console.warn('[deploy] Vercel deployment failed (non-fatal):', err.message);
    }
  }

  // If no Vercel, deployment is via GitLab CI/CD (only if GitLab)
  if (!result.deployUrl) {
    if (useGitLab) {
      const subdomain = projectSlug.replace(/[^a-z0-9-]/g, '-');
      result.deployUrl = `https://${subdomain}.${k8s_domain.replace('*.', '')}`;
      result.method = 'gitlab-ci';
      console.log(`[deploy] Deploy URL will be: ${result.deployUrl} (via GitLab CI/CD)`);
    } else {
      result.method = 'github';
      console.log(`[deploy] GitHub repo ready for manual Vercel connection: ${result.repoUrl}`);
    }
  }

  // ── Step 7: Save deployment to DB ──────────────────
  try {
    await saveDeploymentRecord({
      userId,
      platformProjectId,
      repoUrl: result.repoUrl,
      deployUrl: result.deployUrl,
      method: result.method,
      gitlabProjectId: project?.id || null,
    });
  } catch (err) {
    console.warn('[deploy] DB save failed (non-fatal):', err.message);
  }

  // ── Step 7b: Link project to supabase_projects + chat_sessions ──
  if (platformProjectId) {
    try {
      const supabaseAdmin = await getSupabaseServerClient();
      if (project?.id) {
        await supabaseAdmin
          .from('supabase_projects')
          .update({ gitlab_project_id: project.id })
          .eq('user_id', userId)
          .eq('project_id', platformProjectId);
      }
      // Store platform_repo_url for GitHub repos (used by export-code)
      if (useGitHub && result.repoUrl) {
        await supabaseAdmin
          .from('chat_sessions')
          .update({ platform_repo_url: result.repoUrl })
          .eq('user_id', userId)
          .eq('project_id', platformProjectId)
          .is('platform_repo_url', null);
      }
    } catch (err) {
      console.warn('[deploy] DB link update failed (non-fatal):', err.message);
    }
  }

  result.ok = true;
  return result;
}

// ═══════════════════════════════════════════════════════════
//  Vercel Deployment
// ═══════════════════════════════════════════════════════════

async function deployToVercel({ projectSlug, repoUrl, token, teamId, provider = 'gitlab' }) {
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };

  const queryParams = teamId ? `?teamId=${teamId}` : '';

  // Create a Vercel project linked to the GitLab repo
  const createRes = await fetch(`${VERCEL_API}/v10/projects${queryParams}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: projectSlug,
      framework: 'nextjs',
      gitRepository: {
        type: provider,
        repo: repoUrl,
      },
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!createRes.ok) {
    const err = await createRes.text();
    throw new Error(`Vercel project creation failed: ${err}`);
  }

  const vercelProject = await createRes.json();

  // Trigger deployment
  const deployRes = await fetch(`${VERCEL_API}/v13/deployments${queryParams}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: projectSlug,
      project: vercelProject.id,
      target: 'production',
      gitSource: {
        type: provider,
        ref: 'main',
        repoId: repoUrl,
      },
    }),
    signal: AbortSignal.timeout(30000),
  });

  if (!deployRes.ok) {
    const err = await deployRes.text();
    throw new Error(`Vercel deployment failed: ${err}`);
  }

  const deployment = await deployRes.json();

  // Poll until ready
  const deployUrl = await pollVercelDeployment({
    deploymentId: deployment.id,
    token,
    teamId,
  });

  return deployUrl;
}

async function pollVercelDeployment({ deploymentId, token, teamId }) {
  const headers = { Authorization: `Bearer ${token}` };
  const queryParams = teamId ? `?teamId=${teamId}` : '';

  for (let i = 0; i < 60; i++) {
    await sleep(5000);

    const res = await fetch(
      `${VERCEL_API}/v13/deployments/${deploymentId}${queryParams}`,
      { headers, signal: AbortSignal.timeout(10000) }
    );

    if (res.ok) {
      const data = await res.json();
      if (data.readyState === 'READY') {
        return `https://${data.url}`;
      }
      if (data.readyState === 'ERROR') {
        throw new Error('Vercel deployment failed');
      }
      console.log(`[deploy] Vercel status: ${data.readyState}`);
    }
  }

  throw new Error('Vercel deployment timed out');
}

// ═══════════════════════════════════════════════════════════
//  Repo Export (transfer to user's account)
// ═══════════════════════════════════════════════════════════

/**
 * Export/transfer the generated repo to the user's connected
 * GitHub or GitLab account.
 *
 * @param {object} opts
 * @param {number} opts.gitlabProjectId
 * @param {string} opts.targetNamespace — user's namespace
 * @param {object} opts.settings — deployment settings
 */
export async function exportRepo({ gitlabProjectId, targetNamespace, settings }) {
  const gitlab = createGitLabClient({
    host: settings.gitlab_host,
    token: settings.gitlab_token,
  });

  // Transfer repo to the user's namespace
  const result = await gitlab.transferProject({
    projectId: gitlabProjectId,
    targetNamespace,
  });

  return {
    ok: true,
    newUrl: result.web_url,
  };
}

// ═══════════════════════════════════════════════════════════
//  DB Record
// ═══════════════════════════════════════════════════════════

async function saveDeploymentRecord({
  userId,
  platformProjectId,
  repoUrl,
  deployUrl,
  method,
  gitlabProjectId,
}) {
  const supabase = await getSupabaseServerClient();

  const { error } = await supabase
    .from('project_deployments')
    .upsert({
      user_id: userId,
      project_id: platformProjectId,
      repo_url: repoUrl,
      deploy_url: deployUrl,
      deploy_method: method,
      gitlab_project_id: gitlabProjectId,
      status: 'deployed',
      deployed_at: new Date().toISOString(),
    }, { onConflict: 'user_id,project_id' });

  if (error) throw error;
}

// ═══════════════════════════════════════════════════════════
//  GitHub Repo Creation + Push
// ═══════════════════════════════════════════════════════════

async function createGitHubRepo({ token, repoName, isPrivate = true }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
  };

  const createRes = await fetch('https://api.github.com/user/repos', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: repoName,
      private: isPrivate,
      auto_init: true,
      description: 'Generated by Lucid AI',
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!createRes.ok) {
    const err = await createRes.json().catch(() => ({}));
    throw new Error(err.message || `GitHub API error: ${createRes.status}`);
  }

  const repo = await createRes.json();
  return {
    fullName: repo.full_name,
    htmlUrl: repo.html_url,
    id: repo.id,
  };
}

async function pushToGitHub({ token, fullName, files, commitMessage }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
  };

  // 1. Get the default branch ref (retry — auto_init may take a few seconds)
  let baseSha;
  for (let attempt = 0; attempt < 5; attempt++) {
    await sleep(attempt === 0 ? 1500 : 2000); // first wait shorter
    for (const branch of ['main', 'master']) {
      try {
        const refRes = await fetch(
          `https://api.github.com/repos/${fullName}/git/ref/heads/${branch}`,
          { headers, signal: AbortSignal.timeout(10000) }
        );
        if (refRes.ok) {
          const ref = await refRes.json();
          baseSha = ref.object.sha;
          break;
        }
      } catch { /* try next branch */ }
    }
    if (baseSha) break;
    console.log(`[deploy] Waiting for GitHub auto_init (attempt ${attempt + 1}/5)...`);
  }

  if (!baseSha) throw new Error('Could not get default branch ref after 5 retries — GitHub auto_init may have failed');

  // 2. Create blobs for each file (in batches of 10)
  const BATCH_SIZE = 10;
  const treeItems = [];

  for (let i = 0; i < files.length; i += BATCH_SIZE) {
    const batch = files.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (f) => {
        for (let retry = 0; retry < 2; retry++) {
          try {
            const blobRes = await fetch(
              `https://api.github.com/repos/${fullName}/git/blobs`,
              {
                method: 'POST',
                headers,
                body: JSON.stringify({ content: f.content, encoding: 'utf-8' }),
                signal: AbortSignal.timeout(15000),
              }
            );
            // Rate limited — wait and retry
            if (blobRes.status === 403 || blobRes.status === 429) {
              const retryAfter = parseInt(blobRes.headers.get('retry-after') || '5', 10);
              console.warn(`[deploy] Rate limited creating blob for ${f.path}, waiting ${retryAfter}s...`);
              await sleep(retryAfter * 1000);
              continue;
            }
            if (!blobRes.ok) {
              console.warn(`[deploy] Failed to create blob for ${f.path}: ${blobRes.status}`);
              return null;
            }
            const blob = await blobRes.json();
            return { path: f.path, sha: blob.sha, mode: '100644', type: 'blob' };
          } catch (err) {
            console.warn(`[deploy] Blob creation error for ${f.path}:`, err.message);
            if (retry === 0) await sleep(1000);
          }
        }
        return null;
      })
    );
    treeItems.push(...results.filter(Boolean));
    // Small delay between batches to avoid rate limiting
    if (i + BATCH_SIZE < files.length) await sleep(500);
  }

  const skipped = files.length - treeItems.length;
  if (skipped > 0) console.warn(`[deploy] ${skipped}/${files.length} files skipped due to errors`);
  if (treeItems.length === 0) throw new Error('No files were uploaded — all blob creations failed');

  // 3. Create tree
  const treeRes = await fetch(
    `https://api.github.com/repos/${fullName}/git/trees`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({ base_tree: baseSha, tree: treeItems }),
      signal: AbortSignal.timeout(30000),
    }
  );
  if (!treeRes.ok) {
    const errBody = await treeRes.text().catch(() => '');
    throw new Error(`Failed to create git tree (${treeRes.status}): ${errBody.slice(0, 200)}`);
  }
  const tree = await treeRes.json();

  // 4. Create commit
  const commitRes = await fetch(
    `https://api.github.com/repos/${fullName}/git/commits`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({
        message: commitMessage,
        tree: tree.sha,
        parents: [baseSha],
      }),
      signal: AbortSignal.timeout(15000),
    }
  );
  if (!commitRes.ok) {
    const errBody = await commitRes.text().catch(() => '');
    throw new Error(`Failed to create commit (${commitRes.status}): ${errBody.slice(0, 200)}`);
  }
  const commit = await commitRes.json();

  // 5. Update ref
  await fetch(
    `https://api.github.com/repos/${fullName}/git/refs/heads/main`,
    {
      method: 'PATCH',
      headers,
      body: JSON.stringify({ sha: commit.sha, force: true }),
      signal: AbortSignal.timeout(10000),
    }
  );

  console.log(`[deploy] ✓ Pushed ${treeItems.length}/${files.length} files to GitHub`);
}

// ═══════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function extractRepoPath(url) {
  // "https://gitlab.udevs.io/ops/deployments" → "ops/deployments"
  try {
    const u = new URL(url);
    return u.pathname.replace(/^\//, '').replace(/\.git$/, '').replace(/\/+$/, '');
  } catch {
    return url.replace(/\/+$/, '');
  }
}

function generateGitignore(stack) {
  const common = `node_modules/
.env
.env.local
.env.production
.DS_Store
*.log
`;

  const stackSpecific = {
    nextjs: `.next/
out/
`,
    react: `dist/
build/
`,
    vue: `dist/
`,
    angular: `dist/
.angular/
`,
  };

  return common + (stackSpecific[stack] || '');
}
