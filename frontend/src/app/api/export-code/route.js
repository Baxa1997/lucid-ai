import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';
import { generateDockerfile } from '@/lib/templates/dockerfile';
import { generateNginxConf } from '@/lib/templates/nginx';
import { generateGitlabCI } from '@/lib/templates/cicd';
import { generateMakefile } from '@/lib/templates/makefile';
import { generateOpsFolder } from '@/lib/templates/ops';
import { canExportCode } from '@/lib/subscription';

// ─────────────────────────────────────────────────────────
//  POST /api/export-code
//
//  3-step export flow:
//    Step 1: Create repo on user's provider (GitHub/GitLab/Bitbucket)
//    Step 2: Fetch source files → push to new repo
//    Step 3: (optional) Add CI/CD files in a separate commit
//
//  Body: { projectId, provider, repoName, isPrivate, includeCICD }
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

  const { projectId, provider, repoName, isPrivate = true, includeCICD = false, gitlabInviteUser, godaddyAccountId } = body;

  // ── Pro gate: export is a paid feature ─────────────────
  const exportCheck = await canExportCode(ctx.userId);
  if (!exportCheck.allowed) {
    return NextResponse.json(
      { error: exportCheck.reason, upgradeRequired: true },
      { status: 403 }
    );
  }

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

  const cleanName = repoName.trim().toLowerCase().replace(/[^a-z0-9-_.]/g, '-');

  // ══════════════════════════════════════════════════════
  //  PRE-CHECK: Validate token before doing anything
  //  Catches expired/revoked tokens early with clear error
  // ══════════════════════════════════════════════════════
  try {
    const tokenValid = await validateProviderToken(provider, integration);
    if (!tokenValid.ok) {
      return NextResponse.json(
        {
          error: `Your ${provider === 'github' ? 'GitHub' : provider === 'gitlab' ? 'GitLab' : 'Bitbucket'} token has expired or been revoked. Please reconnect with a new token.`,
          needsReconnect: true,
          provider,
        },
        { status: 401 }
      );
    }
  } catch (err) {
    console.warn('[export-code] Token validation failed:', err.message);
    // Non-fatal — proceed anyway, repo creation will catch actual errors
  }

  // ══════════════════════════════════════════════════════
  //  STEP 1: Create repo FIRST (before fetching files)
  //  This always succeeds if the token is valid.
  // ══════════════════════════════════════════════════════
  let repoUrl;
  let repoId; // GitLab project ID or GitHub repo full name
  let defaultBranch = 'main';
  let gitlabPathWithNamespace = '';

  try {
    if (provider === 'github') {
      const result = await createGitHubRepo({
        token: integration.token,
        repoName: cleanName,
        isPrivate,
      });
      repoUrl = result.htmlUrl;
      repoId = result.fullName;
    } else if (provider === 'gitlab') {
      const result = await createGitLabRepo({
        token: integration.token,
        host: integration.host || 'https://gitlab.com',
        repoName: cleanName,
        isPrivate,
      });
      repoUrl = result.webUrl;
      repoId = result.id;
      defaultBranch = result.defaultBranch;
      gitlabPathWithNamespace = result.pathWithNamespace;
    } else if (provider === 'bitbucket') {
      const result = await createBitbucketRepo({
        username: integration.username,
        appPassword: integration.token,
        repoName: cleanName,
        isPrivate,
      });
      repoUrl = result.htmlUrl;
      repoId = result.fullName;
    }
  } catch (err) {
    console.error('[export-code] Step 1 failed — repo creation:', err.message);
    const isBadCreds = /bad credentials|unauthorized|401|403|invalid token/i.test(err.message);
    return NextResponse.json(
      {
        error: isBadCreds
          ? `Your ${provider} token is invalid. Please reconnect with a new token.`
          : `Failed to create repository: ${err.message}`,
        needsReconnect: isBadCreds,
        provider: isBadCreds ? provider : undefined,
      },
      { status: isBadCreds ? 401 : 500 }
    );
  }

  console.log(`[export-code] ✓ Step 1 complete — repo created: ${repoUrl}`);

  // ══════════════════════════════════════════════════════
  //  STEP 2: Fetch source files + push to new repo
  // ══════════════════════════════════════════════════════
  const supabase = await getSupabaseServerClient();

  // ── Load user's deployment settings (from Settings → Deployment tab) ──
  let deploySettings = {};
  try {
    const { data: settingsRow } = await supabase
      .from('user_settings')
      .select(`
        gitlab_host, gitlab_group, k8s_namespace, k8s_domain, k8s_tls_secret, registry_url, ops_repo_url, ops_repo_branch,
        gitlab_invite_username, godaddy_domain, godaddy_record_type, godaddy_target,
        godaddy_api_key_enc, godaddy_api_key_iv, godaddy_api_secret_enc, godaddy_api_secret_iv
      `)
      .eq('user_id', ctx.userId)
      .maybeSingle();
    if (settingsRow) deploySettings = settingsRow;
  } catch (e) {
    console.warn('[export-code] Could not load user deployment settings:', e.message);
  }

  // Load GoDaddy account if selected
  let gdAccount = null;
  let godaddyApiKey = '';
  let godaddyApiSecret = '';
  if (godaddyAccountId) {
    try {
      const { data: acc } = await supabase
        .from('godaddy_accounts')
        .select('domain, record_type, target, api_key_enc, api_key_iv, api_secret_enc, api_secret_iv')
        .eq('id', godaddyAccountId)
        .eq('user_id', ctx.userId)
        .maybeSingle();
      if (acc) {
        gdAccount = acc;
        const { decrypt } = await import('@/lib/crypto');
        if (acc.api_key_enc && acc.api_key_iv) godaddyApiKey = decrypt(acc.api_key_enc, acc.api_key_iv);
        if (acc.api_secret_enc && acc.api_secret_iv) godaddyApiSecret = decrypt(acc.api_secret_enc, acc.api_secret_iv);
      }
    } catch (e) {
      console.warn('[export-code] Failed to load GoDaddy account:', e.message);
    }
  } else {
    // Legacy fallback: decrypt from user_settings
    try {
      const { decrypt } = await import('@/lib/crypto');
      if (deploySettings.godaddy_api_key_enc && deploySettings.godaddy_api_key_iv) {
        godaddyApiKey = decrypt(deploySettings.godaddy_api_key_enc, deploySettings.godaddy_api_key_iv);
      }
      if (deploySettings.godaddy_api_secret_enc && deploySettings.godaddy_api_secret_iv) {
        godaddyApiSecret = decrypt(deploySettings.godaddy_api_secret_enc, deploySettings.godaddy_api_secret_iv);
      }
    } catch (e) {
      console.warn('[export-code] Failed to decrypt GoDaddy API keys:', e.message);
    }
  }

  // Merge with defaults
  const cicdConfig = {
    registryUrl: deploySettings.registry_url || 'gitlab.udevs.io:5050',
    k8sNamespace: deploySettings.k8s_namespace || 'frontend-prod',
    k8sDomain: deploySettings.k8s_domain || '*.javoxir.online',
    gitlabGroup: deploySettings.gitlab_group || body.gitlabGroup || '',
    opsRepoBranch: deploySettings.ops_repo_branch || 'master',
  };
  console.log('[export-code] CI/CD config:', JSON.stringify(cicdConfig));

  // Get project metadata for file fetching
  const { data: deployment } = await supabase
    .from('project_deployments')
    .select('repo_url, gitlab_project_id')
    .eq('user_id', ctx.userId)
    .eq('project_id', projectId)
    .maybeSingle();

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

  // Fetch files from all sources
  let files = [];
  let fetchWarning = '';

  console.log(`[export-code] File sources: platformRepoUrl=${platformRepoUrl || 'NONE'}, gitlabProjectId=${deployment?.gitlab_project_id || 'NONE'}, chatSessions=checking...`);

  // Source 1: Platform GitHub repo (wizard-created projects)
  if (files.length === 0 && platformRepoUrl) {
    try {
      // Try platform token first, then user's own GitHub token as fallback
      const platformToken = process.env.PLATFORM_GITHUB_TOKEN || '';
      const userGithubToken = meta.github_integration?.token || '';
      const readToken = platformToken || userGithubToken;

      console.log(`[export-code] Source 1: platformRepoUrl=${platformRepoUrl}, platformToken=${platformToken ? 'SET' : 'MISSING'}, userToken=${userGithubToken ? 'SET' : 'MISSING'}`);

      if (readToken) {
        files = await fetchGitHubRepoFiles(platformRepoUrl, readToken);
        if (files.length > 0) {
          console.log(`[export-code] ✓ Fetched ${files.length} files from platform GitHub`);
        } else {
          console.warn(`[export-code] Platform GitHub returned 0 files for ${platformRepoUrl}`);
          fetchWarning = 'Platform repo exists but contains no readable files';
        }
      } else {
        console.warn('[export-code] No token available to read platform GitHub repo');
        fetchWarning = 'Platform GitHub token not configured. Add PLATFORM_GITHUB_TOKEN to frontend/.env';
      }
    } catch (err) {
      console.error('[export-code] Platform GitHub fetch failed:', err.message);
      fetchWarning = `Platform GitHub read failed: ${err.message}`;
    }
  }

  // Source 2: Platform GitLab (legacy deployment pipeline)
  if (files.length === 0 && deployment?.gitlab_project_id) {
    try {
      files = await fetchPlatformGitLabFiles(deployment);
      if (files.length > 0) {
        console.log(`[export-code] ✓ Fetched ${files.length} files from platform GitLab`);
      }
    } catch (err) {
      console.error('[export-code] Platform GitLab fetch failed:', err.message);
    }
  }

  // Source 3: Agent workspace (scratch sessions)
  if (files.length === 0) {
    try {
      files = await fetchWorkspaceFiles(ctx.userId, ctx.accessToken, projectId);
      if (files.length > 0) {
        console.log(`[export-code] ✓ Fetched ${files.length} files from workspace`);
      }
    } catch (err) {
      console.error('[export-code] Workspace fetch failed:', err.message);
    }
  }

  // If still no files — track warning but DON'T return early (CI/CD files still need to be pushed)
  let exportWarning = '';
  let filesExported = 0;

  if (files.length === 0) {
    exportWarning = 'Repository created but no source files found to push. ' +
      'The project may not have generated code yet, or the workspace was cleaned up. ' +
      'You can push code manually later.' +
      (fetchWarning ? ` (${fetchWarning})` : '');
    console.warn('[export-code] No source files found — skipping Step 2, continuing to Step 3');
  } else {
    // Push application files to the new repo
    try {
      if (provider === 'github') {
        await pushToGitHub({
          token: integration.token,
          fullName: repoId,
          files,
          commitMessage: '🚀 Exported from Lucid AI',
        });
      } else if (provider === 'gitlab') {
        await pushToGitLab({
          token: integration.token,
          host: integration.host || 'https://gitlab.com',
          projectId: repoId,
          files,
          branch: defaultBranch,
          commitMessage: '🚀 Exported from Lucid AI',
        });
      } else if (provider === 'bitbucket') {
        await pushToBitbucket({
          username: integration.username,
          appPassword: integration.token,
          fullName: repoId,
          files,
          commitMessage: '🚀 Exported from Lucid AI',
        });
      }
      filesExported = files.length;
      console.log(`[export-code] ✓ Step 2 complete — ${files.length} files pushed`);
    } catch (err) {
      console.error('[export-code] Step 2 failed — file push:', err.message);
      exportWarning = `Repository created but file push failed: ${err.message}`;
      // DON'T return — fall through to Step 3 so CI/CD files can still be pushed
    }
  }

  // ══════════════════════════════════════════════════════
  //  STEP 3: Add CI/CD files (separate commit)
  // ══════════════════════════════════════════════════════
  let cicdAdded = false;

  if (includeCICD) {
    try {
      const hasNextConfig = files.some(f => /next\.config/i.test(f.path));
      const hasPackageJson = files.some(f => f.path === 'package.json');
      const packageManager = files.some(f => f.path === 'yarn.lock') ? 'yarn' 
        : files.some(f => f.path === 'pnpm-lock.yaml') ? 'pnpm' 
        : files.some(f => f.path === 'bun.lockb') ? 'bun' 
        : 'npm';
        
      const stack = hasNextConfig ? 'nextjs' : hasPackageJson ? 'react' : 'html-css';
      const docker = generateDockerfile({ stack, packageManager });

      // ── Step 3a: CI/CD files for the project repo ──────
      const cicdFiles = [
        { path: 'Dockerfile', content: docker.content },
        { path: 'nginx.conf', content: generateNginxConf() },
        { path: 'Makefile', content: generateMakefile({ projectSlug: cleanName, projectName: gitlabPathWithNamespace || cicdConfig.gitlabGroup || cleanName }) },
        { path: '.gitlab-ci.yml', content: generateGitlabCI() },
      ];

      // Don't overwrite files already in the repo
      const existingPaths = new Set(files.map(f => f.path));
      const newCicdFiles = cicdFiles.filter(f => !existingPaths.has(f.path));

      if (newCicdFiles.length > 0) {
        if (provider === 'github') {
          await pushToGitHub({
            token: integration.token,
            fullName: repoId,
            files: newCicdFiles,
            commitMessage: '🔧 Add CI/CD pipeline — Lucid AI',
          });
        } else if (provider === 'gitlab') {
          await pushToGitLab({
            token: integration.token,
            host: integration.host || 'https://gitlab.com',
            projectId: repoId,
            files: newCicdFiles,
            branch: defaultBranch,
            commitMessage: '🔧 Add CI/CD pipeline — Lucid AI',
          });
        } else if (provider === 'bitbucket') {
          await pushToBitbucket({
            username: integration.username,
            appPassword: integration.token,
            fullName: repoId,
            files: newCicdFiles,
            commitMessage: '🔧 Add CI/CD pipeline — Lucid AI',
          });
        }
        cicdAdded = true;
        console.log(`[export-code] ✓ Step 3a — CI/CD files added: ${newCicdFiles.map(f => f.path).join(', ')}`);
      }

      // ── Step 3b: Create ops folder in ops repo ──────
      // Uses the user's connected GitLab token to push to the ops deployments repo
      const gitlabUrl = process.env.GITLAB_URL || 'https://gitlab.udevs.io';
      const opsRepoId = process.env.OPS_REPO_PROJECT_ID || '';
      // Use user's GitLab token if they exported to GitLab, otherwise skip
      const opsToken = (provider === 'gitlab' && integration.token) ? integration.token : '';

      if (opsToken && opsRepoId) {
        try {
          const repoPath = gitlabPathWithNamespace || (cicdConfig.gitlabGroup
            ? `${cicdConfig.gitlabGroup}/${cleanName}`
            : cleanName);

          // If the domain contains a wildcard (*), replace it with the repo name. 
          // Otherwise, use the exact domain the user provided in settings.
          const settingDomain = cicdConfig.k8sDomain || '*.javoxir.online';
          const projectDomain = settingDomain.includes('*.') 
            ? settingDomain.replace('*.', `${cleanName}.`)
            : settingDomain;

          const ops = generateOpsFolder({
            projectSlug: cleanName,
            repoPath,
            registryUrl: cicdConfig.registryUrl,
            servicePort: docker.expose,
            domain: projectDomain,
          });

          // Look up ops repo default branch (likely 'master')
          let opsBranch = 'master';
          const opsInfoRes = await fetch(`${gitlabUrl}/api/v4/projects/${opsRepoId}`, {
            headers: { 'PRIVATE-TOKEN': opsToken },
            signal: AbortSignal.timeout(10000),
          });
          if (opsInfoRes.ok) {
            const opsInfo = await opsInfoRes.json();
            if (opsInfo.default_branch) opsBranch = opsInfo.default_branch;
          }

          // Check which files already exist in the ops path
          const opsBasePath = `clusters/cluster-prod/frontend-prod/${cleanName}`;
          let existingOpsFiles = new Set();
          const treeRes = await fetch(
            `${gitlabUrl}/api/v4/projects/${opsRepoId}/repository/tree?path=${encodeURIComponent(opsBasePath)}&recursive=true&ref=${opsBranch}`,
            { headers: { 'PRIVATE-TOKEN': opsToken }, signal: AbortSignal.timeout(10000) }
          );
          if (treeRes.ok) {
            const tree = await treeRes.json();
            tree.forEach(t => existingOpsFiles.add(t.path));
          }

          // Push ops files via GitLab Commits API
          const opsActions = ops.files.map(f => ({
            action: existingOpsFiles.has(f.path) ? 'update' : 'create',
            file_path: f.path,
            content: f.content,
          }));

          console.log(`[export-code] Step 3b — pushing ops files: ${opsActions.map(a => `${a.action} ${a.file_path}`).join(', ')}`);

          const opsResp = await fetch(
            `${gitlabUrl}/api/v4/projects/${opsRepoId}/repository/commits`,
            {
              method: 'POST',
              headers: {
                'PRIVATE-TOKEN': opsToken,
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({
                branch: opsBranch,
                commit_message: `Add ${cleanName} deployment — Lucid AI`,
                actions: opsActions,
              }),
              signal: AbortSignal.timeout(20000),
            }
          );

          if (opsResp.ok) {
            console.log(`[export-code] ✓ Step 3b — ops folder created for ${cleanName}`);
          } else {
            const errText = await opsResp.text().catch(() => '');
            console.warn(`[export-code] Step 3b — ops push ${opsResp.status}: ${errText.slice(0, 300)}`);
          }
        } catch (opsErr) {
          console.warn('[export-code] Step 3b — ops folder (non-fatal):', opsErr.message);
        }
      }

      // ── Step 3c: Add GitLab CI/CD variables ──────
      if (provider === 'gitlab' && integration.token && repoId) {
        const glHost = integration.host || 'https://gitlab.com';
        const varsToSet = [
          { key: 'K8S_NAMESPACE_PROD', value: cicdConfig.k8sNamespace },
          { key: 'APP_NAME', value: cleanName },
        ];

        for (const v of varsToSet) {
          try {
            const varResp = await fetch(
              `${glHost}/api/v4/projects/${repoId}/variables`,
              {
                method: 'POST',
                headers: {
                  'PRIVATE-TOKEN': integration.token,
                  'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                  key: v.key,
                  value: v.value,
                  protected: false,
                  masked: false,
                  environment_scope: '*',
                }),
                signal: AbortSignal.timeout(10000),
              }
            );
            if (varResp.ok || varResp.status === 409) {
              console.log(`[export-code] ✓ Step 3c — variable ${v.key} set`);
            } else {
              console.warn(`[export-code] Step 3c — variable ${v.key}: ${varResp.status}`);
            }
          } catch (varErr) {
            console.warn(`[export-code] Step 3c — variable ${v.key} (non-fatal):`, varErr.message);
          }
        }
      }

    } catch (err) {
      console.warn('[export-code] Step 3 failed — CI/CD (non-fatal):', err.message);
      // Non-fatal — repo + code already pushed successfully
    }
  }

  // ══════════════════════════════════════════════════════
  //  STEP 4: Automations (GitLab auto-invite & GoDaddy DNS)
  // ══════════════════════════════════════════════════════
  let automationsWarning = '';

  // ── Auto Invite ──
  const targetInviteUser = gitlabInviteUser?.trim() || deploySettings.gitlab_invite_username?.trim();
  
  if (repoId && integration?.token && targetInviteUser) {
    if (provider === 'gitlab') {
      try {
        const glHost = (deploySettings.gitlab_host || integration.host || 'https://gitlab.com').replace(/\/+$/, '');
        
        // 1. Look up user ID by username
        const userRes = await fetch(`${glHost}/api/v4/users?username=${encodeURIComponent(targetInviteUser)}`, {
          headers: { 'PRIVATE-TOKEN': integration.token },
          signal: AbortSignal.timeout(8000),
        });

        if (userRes.ok) {
          const users = await userRes.json();
          if (users && users.length > 0) {
            const inviteUserId = users[0].id;
            
            // 2. Add as Owner (Access Level 50)
            const inviteRes = await fetch(`${glHost}/api/v4/projects/${repoId}/members`, {
              method: 'POST',
              headers: {
                'PRIVATE-TOKEN': integration.token,
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({
                user_id: inviteUserId,
                access_level: 50,
              }),
              signal: AbortSignal.timeout(8000),
            });

            if (inviteRes.ok || inviteRes.status === 409) {
              console.log(`[export-code] ✓ Step 4a — GitLab user '${targetInviteUser}' invited to project.`);
            } else {
              const errBody = await inviteRes.text().catch(() => '');
              console.warn(`[export-code] Step 4a — Failed to invite user (${inviteRes.status}):`, errBody.slice(0, 200));
              automationsWarning += `Failed to invite GitLab user '${targetInviteUser}'. `;
            }
          } else {
            console.warn(`[export-code] Step 4a — GitLab user '${targetInviteUser}' not found.`);
            automationsWarning += `GitLab user '${targetInviteUser}' not found. `;
          }
        }
      } catch (e) {
        console.warn(`[export-code] Step 4a — GitLab invite error:`, e.message);
        automationsWarning += `GitLab invite error: ${e.message}. `;
      }
    } else if (provider === 'github') {
      try {
        // GitHub: add as admin collaborator — repoId is "owner/repo"
        const inviteRes = await fetch(`https://api.github.com/repos/${repoId}/collaborators/${encodeURIComponent(targetInviteUser)}`, {
          method: 'PUT',
          headers: {
            'Authorization': `Bearer ${integration.token}`,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
          },
          body: JSON.stringify({ permission: 'admin' }),
          signal: AbortSignal.timeout(8000),
        });

        if (inviteRes.ok || inviteRes.status === 201 || inviteRes.status === 204) {
          console.log(`[export-code] ✓ Step 4a — GitHub user '${targetInviteUser}' invited as collaborator.`);
        } else {
          const errBody = await inviteRes.text().catch(() => '');
          console.warn(`[export-code] Step 4a — Failed to invite GitHub user (${inviteRes.status}):`, errBody.slice(0, 200));
          automationsWarning += `Failed to invite GitHub user '${targetInviteUser}'. `;
        }
      } catch (e) {
        console.warn(`[export-code] Step 4a — GitHub invite error:`, e.message);
        automationsWarning += `GitHub invite error: ${e.message}. `;
      }
    }
  }

  // ── GoDaddy DNS Automation ──
  // Prefer the selected gdAccount; fallback to deploySettings
  const gdDomain = gdAccount?.domain?.trim() || deploySettings.godaddy_domain?.trim();
  const gdTarget = gdAccount?.target?.trim() || deploySettings.godaddy_target?.trim();
  const gdType = gdAccount?.record_type?.trim() || deploySettings.godaddy_record_type?.trim() || 'A';

  if (gdDomain && gdTarget && godaddyApiKey && godaddyApiSecret) {
    try {
      console.log(`[export-code] Step 4b — Configuring GoDaddy DNS for ${cleanName}.${gdDomain} -> ${gdTarget} (${gdType})`);
      
      const godaddyUrl = `https://api.godaddy.com/v1/domains/${encodeURIComponent(gdDomain)}/records/${gdType}/${encodeURIComponent(cleanName)}`;
      
      const dnsRes = await fetch(godaddyUrl, {
        method: 'PUT',
        headers: {
          'Authorization': `sso-key ${godaddyApiKey}:${godaddyApiSecret}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify([{
          data: gdTarget,
          name: cleanName,
          ttl: 600,
        }]),
        signal: AbortSignal.timeout(10000),
      });

      if (dnsRes.ok) {
        console.log(`[export-code] ✓ Step 4b — GoDaddy DNS record configured successfully.`);
      } else {
        const errBody = await dnsRes.text().catch(() => '');
        console.warn(`[export-code] Step 4b — GoDaddy DNS error (${dnsRes.status}):`, errBody.slice(0, 300));
        
        if (dnsRes.status === 403) {
          automationsWarning += `GoDaddy API access denied. Your account may need 10+ domains for API access, or regenerate your API key at developer.godaddy.com/keys with Production access. DNS record not created — add it manually: Type=${gdType}, Name=${cleanName}, Value=${gdTarget}, TTL=600. `;
        } else if (dnsRes.status === 401) {
          automationsWarning += `GoDaddy API key is invalid or expired. Please update your credentials in Settings. `;
        } else if (dnsRes.status === 422) {
          automationsWarning += `GoDaddy rejected the DNS record. Check that domain "${gdDomain}" is in your account. `;
        } else {
          automationsWarning += `GoDaddy DNS error (${dnsRes.status}). `;
        }
      }
    } catch (e) {
      console.warn(`[export-code] Step 4b — GoDaddy DNS error:`, e.message);
      automationsWarning += `GoDaddy DNS API error: ${e.message}. `;
    }
  }

  // ── Save records ─────────────────────────────────────
  await saveExportRecord(supabase, ctx.userId, projectId, provider, repoUrl, cleanName);

  // Save user repo URL to chat_sessions
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
    filesExported,
    cicdAdded,
    ...(exportWarning || automationsWarning ? { warning: [exportWarning, automationsWarning].filter(Boolean).join(' | ') } : {}),
  });
}


// ═══════════════════════════════════════════════════════════
//  Repo Creation (Step 1)
// ═══════════════════════════════════════════════════════════

async function createGitHubRepo({ token, repoName, isPrivate }) {
  const res = await fetch('https://api.github.com/user/repos', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      name: repoName,
      private: isPrivate,
      auto_init: true,
      description: 'Exported from Lucid AI',
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.message || `GitHub API error: ${res.status}`);
  }

  const repo = await res.json();
  return { fullName: repo.full_name, htmlUrl: repo.html_url, id: repo.id };
}

async function createGitLabRepo({ token, host, repoName, isPrivate }) {
  const api = `${host.replace(/\/+$/, '')}/api/v4`;
  const res = await fetch(`${api}/projects`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'PRIVATE-TOKEN': token,
    },
    body: JSON.stringify({
      name: repoName,
      visibility: isPrivate ? 'private' : 'public',
      initialize_with_readme: true,
      description: 'Exported from Lucid AI',
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`GitLab error: ${err}`);
  }

  const project = await res.json();
  return { 
    id: project.id, 
    webUrl: project.web_url, 
    path: project.path, 
    pathWithNamespace: project.path_with_namespace,
    defaultBranch: project.default_branch || 'main' 
  };
}

async function createBitbucketRepo({ username, appPassword, repoName, isPrivate }) {
  const authHeader = `Basic ${Buffer.from(`${username}:${appPassword}`).toString('base64')}`;
  const res = await fetch(
    `https://api.bitbucket.org/2.0/repositories/${username}/${repoName}`,
    {
      method: 'POST',
      headers: { Authorization: authHeader, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scm: 'git',
        is_private: isPrivate,
        description: 'Exported from Lucid AI',
        mainbranch: { type: 'branch', name: 'main' },
      }),
      signal: AbortSignal.timeout(15000),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Bitbucket error: ${err}`);
  }

  const repo = await res.json();
  return {
    fullName: `${username}/${repoName}`,
    htmlUrl: repo.links?.html?.href || `https://bitbucket.org/${username}/${repoName}`,
  };
}


// ═══════════════════════════════════════════════════════════
//  File Fetching (Step 2 — sources)
// ═══════════════════════════════════════════════════════════

async function fetchGitHubRepoFiles(repoUrl, token) {
  // Parse "https://github.com/Owner/repo-name" → Owner/repo-name
  const repoPath = repoUrl
    .replace('https://github.com/', '')
    .replace(/\.git$/, '')
    .replace(/\/$/, '');

  if (!repoPath || !repoPath.includes('/')) return [];

  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
  };

  // Get the full file tree recursively — track which branch works
  let treeData;
  let foundBranch = 'main';
  for (const branch of ['main', 'master']) {
    try {
      const res = await fetch(
        `https://api.github.com/repos/${repoPath}/git/trees/${branch}?recursive=1`,
        { headers, signal: AbortSignal.timeout(15000) }
      );
      if (res.ok) {
        treeData = await res.json();
        foundBranch = branch;
        console.log(`[export-code] GitHub tree found on branch '${branch}', ${(treeData.tree || []).length} items`);
        break;
      }
    } catch { /* try next branch */ }
  }

  if (!treeData) {
    console.warn(`[export-code] GitHub tree not found for ${repoPath} on main/master`);
    return [];
  }

  // Filter to blobs, skip large/binary files and .git internals
  const blobs = (treeData.tree || []).filter(
    (t) => t.type === 'blob' && (t.size || 0) < 500_000 && !t.path.startsWith('.git/')
  );

  console.log(`[export-code] Fetching ${blobs.length} blobs from ${repoPath} (branch: ${foundBranch})`);

  const BATCH_SIZE = 10;
  const files = [];

  for (let i = 0; i < blobs.length; i += BATCH_SIZE) {
    const batch = blobs.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (blob) => {
        try {
          // Use blob SHA directly — works regardless of branch name
          const res = await fetch(
            `https://api.github.com/repos/${repoPath}/git/blobs/${blob.sha}`,
            { headers, signal: AbortSignal.timeout(10000) }
          );
          if (!res.ok) return null;
          const data = await res.json();
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
    if (i + BATCH_SIZE < blobs.length) await sleep(300);
  }

  return files;
}

async function fetchPlatformGitLabFiles(deployment) {
  const gitlabToken = process.env.SUPABASE_ACCESS_TOKEN || process.env.GITLAB_TOKEN || '';
  const gitlabHost = process.env.GITLAB_HOST || 'https://gitlab.udevs.io';

  if (!deployment?.gitlab_project_id || !gitlabToken) return [];

  const api = `${gitlabHost}/api/v4`;
  const treeRes = await fetch(
    `${api}/projects/${deployment.gitlab_project_id}/repository/tree?recursive=true&per_page=100&ref=main`,
    { headers: { 'PRIVATE-TOKEN': gitlabToken }, signal: AbortSignal.timeout(15000) }
  );
  if (!treeRes.ok) return [];
  const tree = await treeRes.json();
  const blobs = tree.filter(t => t.type === 'blob');

  const BATCH_SIZE = 10;
  const files = [];

  for (let i = 0; i < blobs.length; i += BATCH_SIZE) {
    const batch = blobs.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (blob) => {
        try {
          const res = await fetch(
            `${api}/projects/${deployment.gitlab_project_id}/repository/files/${encodeURIComponent(blob.path)}/raw?ref=main`,
            { headers: { 'PRIVATE-TOKEN': gitlabToken }, signal: AbortSignal.timeout(10000) }
          );
          if (!res.ok) return null;
          return { path: blob.path, content: await res.text() };
        } catch {
          return null;
        }
      })
    );
    files.push(...results.filter(Boolean));
  }

  return files;
}

async function fetchWorkspaceFiles(userId, accessToken, projectId) {
  const AI_SERVICE_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';

  try {
    const { getSupabaseServerClient } = await import('@/lib/supabase/server');
    const supabase = await getSupabaseServerClient();

    const { data: chatSessions } = await supabase
      .from('chat_sessions')
      .select('agent_session_id')
      .eq('user_id', userId)
      .eq('project_id', projectId)
      .order('created_at', { ascending: false })
      .limit(5);

    if (!chatSessions?.length) return [];

    const headers = {};
    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    } else {
      headers['X-User-ID'] = userId;
      const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';
      if (INTERNAL_API_KEY) headers['X-Internal-Key'] = INTERNAL_API_KEY;
    }

    for (const session of chatSessions) {
      if (!session.agent_session_id) continue;
      
      try {
        const res = await fetch(
          `${AI_SERVICE_URL}/api/v1/files/export?session_id=${encodeURIComponent(session.agent_session_id)}`,
          { headers, signal: AbortSignal.timeout(10000) }
        );

        if (res.ok) {
          const data = await res.json();
          if (data.files && data.files.length > 0) {
            return data.files;
          }
        }
      } catch (e) {
        console.warn(`[export-code] Workspace fetch failed for session ${session.agent_session_id}:`, e.message);
      }
    }
    
    return [];
  } catch {
    return [];
  }
}


// ═══════════════════════════════════════════════════════════
//  File Pushing (Step 2+3 — push to user's repo)
// ═══════════════════════════════════════════════════════════

async function pushToGitHub({ token, fullName, files, commitMessage }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
  };

  // Wait for auto_init to propagate
  let baseSha;
  for (let attempt = 0; attempt < 5; attempt++) {
    await sleep(attempt === 0 ? 1500 : 2000);
    for (const branch of ['main', 'master']) {
      try {
        const res = await fetch(
          `https://api.github.com/repos/${fullName}/git/ref/heads/${branch}`,
          { headers, signal: AbortSignal.timeout(10000) }
        );
        if (res.ok) {
          const ref = await res.json();
          baseSha = ref.object.sha;
          break;
        }
      } catch { /* try next */ }
    }
    if (baseSha) break;
  }

  if (!baseSha) throw new Error('Could not get default branch ref');

  // Create blobs in batches
  const BATCH_SIZE = 10;
  const treeItems = [];

  for (let i = 0; i < files.length; i += BATCH_SIZE) {
    const batch = files.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (f) => {
        for (let retry = 0; retry < 2; retry++) {
          try {
            const res = await fetch(
              `https://api.github.com/repos/${fullName}/git/blobs`,
              {
                method: 'POST',
                headers,
                body: JSON.stringify({ content: f.content, encoding: 'utf-8' }),
                signal: AbortSignal.timeout(15000),
              }
            );
            if (res.status === 403 || res.status === 429) {
              await sleep(5000);
              continue;
            }
            if (!res.ok) return null;
            const blob = await res.json();
            return { path: f.path, sha: blob.sha, mode: '100644', type: 'blob' };
          } catch {
            if (retry === 0) await sleep(1000);
          }
        }
        return null;
      })
    );
    treeItems.push(...results.filter(Boolean));
    if (i + BATCH_SIZE < files.length) await sleep(500);
  }

  if (treeItems.length === 0) throw new Error('No files were uploaded');

  // Create tree → commit → update ref
  const treeRes = await fetch(`https://api.github.com/repos/${fullName}/git/trees`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ base_tree: baseSha, tree: treeItems }),
    signal: AbortSignal.timeout(30000),
  });
  if (!treeRes.ok) throw new Error('Failed to create git tree');
  const tree = await treeRes.json();

  const commitRes = await fetch(`https://api.github.com/repos/${fullName}/git/commits`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message: commitMessage, tree: tree.sha, parents: [baseSha] }),
    signal: AbortSignal.timeout(15000),
  });
  if (!commitRes.ok) throw new Error('Failed to create commit');
  const commit = await commitRes.json();

  await fetch(`https://api.github.com/repos/${fullName}/git/refs/heads/main`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ sha: commit.sha, force: true }),
    signal: AbortSignal.timeout(10000),
  });
}

async function pushToGitLab({ token, host, projectId, files, branch = 'main', commitMessage }) {
  const api = `${host.replace(/\/+$/, '')}/api/v4`;
  const headers = { 'Content-Type': 'application/json', 'PRIVATE-TOKEN': token };

  // Wait for the default branch to be ready (initialize_with_readme is async)
  let existingFiles = new Set();
  let branchReady = false;

  for (let attempt = 0; attempt < 6; attempt++) {
    if (attempt > 0) await sleep(2000);

    try {
      const treeRes = await fetch(
        `${api}/projects/${projectId}/repository/tree?recursive=true&per_page=100&ref=${branch}`,
        { headers, signal: AbortSignal.timeout(10000) }
      );
      if (treeRes.ok) {
        const tree = await treeRes.json();
        tree.forEach(t => existingFiles.add(t.path));
        branchReady = true;
        break;
      }
      // 404 = branch not created yet, keep waiting
      if (treeRes.status === 404) {
        console.log(`[export-code] GitLab branch '${branch}' not ready yet (attempt ${attempt + 1}/6)…`);
        continue;
      }
      // Other errors — log but try anyway
      console.warn(`[export-code] GitLab tree fetch returned ${treeRes.status}`);
      branchReady = true; // assume it's ready, just empty
      break;
    } catch (e) {
      console.warn(`[export-code] GitLab tree fetch error (attempt ${attempt + 1}):`, e.message);
    }
  }

  if (!branchReady) {
    console.warn(`[export-code] Branch '${branch}' never became ready — pushing with 'create' actions`);
  }

  const actions = files.map((f) => ({
    action: existingFiles.has(f.path) ? 'update' : 'create',
    file_path: f.path,
    content: f.content,
  }));

  // If branch doesn't exist yet, use start_branch to create it from scratch
  const commitBody = {
    branch,
    commit_message: commitMessage,
    actions,
  };

  // If the branch doesn't exist, tell GitLab to create it with this commit
  if (!branchReady) {
    commitBody.start_branch = branch;
  }

  const res = await fetch(`${api}/projects/${projectId}/repository/commits`, {
    method: 'POST',
    headers,
    body: JSON.stringify(commitBody),
    signal: AbortSignal.timeout(60000),
  });

  if (!res.ok) {
    const err = await res.text();
    console.error(`[export-code] GitLab push failed (project=${projectId}, branch=${branch}):`, err);
    throw new Error(`GitLab push failed: ${err}`);
  }

  console.log(`[export-code] ✓ GitLab push success — ${files.length} files to project ${projectId} on branch '${branch}'`);
}

async function pushToBitbucket({ username, appPassword, fullName, files, commitMessage }) {
  const authHeader = `Basic ${Buffer.from(`${username}:${appPassword}`).toString('base64')}`;
  const boundary = `----LucidExport${Date.now()}`;
  let formBody = '';

  for (const f of files) {
    formBody += `--${boundary}\r\n`;
    formBody += `Content-Disposition: form-data; name="${f.path}"; filename="${f.path}"\r\n`;
    formBody += `Content-Type: application/octet-stream\r\n\r\n`;
    formBody += `${f.content}\r\n`;
  }

  formBody += `--${boundary}\r\n`;
  formBody += `Content-Disposition: form-data; name="message"\r\n\r\n`;
  formBody += `${commitMessage}\r\n`;
  formBody += `--${boundary}\r\n`;
  formBody += `Content-Disposition: form-data; name="branch"\r\n\r\n`;
  formBody += `main\r\n`;
  formBody += `--${boundary}--\r\n`;

  const res = await fetch(
    `https://api.bitbucket.org/2.0/repositories/${fullName}/src`,
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

  if (!res.ok && res.status !== 201) {
    const err = await res.text();
    throw new Error(`Bitbucket push failed: ${err}`);
  }
}


// ═══════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════

async function saveExportRecord(supabase, userId, projectId, provider, repoUrl, repoName) {
  try {
    await supabase.from('project_exports').upsert({
      user_id: userId,
      project_id: projectId,
      provider,
      repo_url: repoUrl,
      repo_name: repoName,
      exported_at: new Date().toISOString(),
    }, { onConflict: 'user_id,project_id,provider' });
  } catch (e) {
    console.warn('[export-code] Could not save export record:', e);
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function validateProviderToken(provider, integration) {
  try {
    if (provider === 'github') {
      const res = await fetch('https://api.github.com/user', {
        headers: {
          Authorization: `Bearer ${integration.token}`,
          Accept: 'application/vnd.github+json',
        },
        signal: AbortSignal.timeout(8000),
      });
      return { ok: res.ok };
    }
    if (provider === 'gitlab') {
      const host = (integration.host || 'https://gitlab.com').replace(/\/+$/, '');
      const res = await fetch(`${host}/api/v4/user`, {
        headers: { 'PRIVATE-TOKEN': integration.token },
        signal: AbortSignal.timeout(8000),
      });
      return { ok: res.ok };
    }
    if (provider === 'bitbucket') {
      const res = await fetch('https://api.bitbucket.org/2.0/user', {
        headers: {
          Authorization: `Basic ${Buffer.from(`${integration.username}:${integration.token}`).toString('base64')}`,
        },
        signal: AbortSignal.timeout(8000),
      });
      return { ok: res.ok };
    }
    return { ok: true };
  } catch {
    return { ok: true }; // Network error — let the real call fail with better error
  }
}
