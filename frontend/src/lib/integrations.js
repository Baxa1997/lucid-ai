// ─────────────────────────────────────────────────────────
//  Lucid AI — Integration helpers
//  GitHub/GitLab PATs are stored encrypted by the backend.
//  Legacy Supabase user_metadata tokens are read only as fallback.
// ─────────────────────────────────────────────────────────

import { getSupabaseBrowserClient } from '@/lib/supabase/client';

function emptyIntegrations() {
  return { github: null, gitlab: null, bitbucket: null };
}

function legacyIntegrationsFromUser(user) {
  const meta = user?.user_metadata || {};
  return {
    github: meta.github_integration || null,
    gitlab: meta.gitlab_integration || null,
    bitbucket: meta.bitbucket_integration || null,
  };
}

function normalizeBackendIntegration(row) {
  const provider = String(row?.provider || '').toLowerCase();
  const username = row?.externalUsername || row?.username || row?.label || '';
  const base = {
    connected: row?.connected !== false,
    username,
    label: row?.label || username,
    scopes: row?.scopes || '',
    connectedAt: row?.connectedAt || row?.createdAt || null,
  };

  if (provider === 'github') return base;
  if (provider === 'gitlab') {
    const host = (row?.gitlabUrl || row?.host || 'https://gitlab.com').replace(/\/+$/, '');
    return { ...base, host, gitlabUrl: host };
  }
  return null;
}

async function cleanupLegacyIntegration(key) {
  try {
    const supabase = getSupabaseBrowserClient();
    await supabase.auth.updateUser({ data: { [key]: null } });
  } catch {
    // Best-effort cleanup. Backend encrypted storage is already the source of truth.
  }
}

function integrationError(data, fallback) {
  return data?.detail || data?.error || data?.message || fallback;
}

/**
 * Read integration data. Backend encrypted rows win; old metadata is fallback
 * so already-connected users still work until they reconnect.
 */
export async function getIntegrations() {
  const supabase = getSupabaseBrowserClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) return emptyIntegrations();

  const legacy = legacyIntegrationsFromUser(user);

  try {
    const res = await fetch('/api/integrations', { cache: 'no-store' });
    if (!res.ok) return legacy;

    const data = await res.json();
    const mapped = emptyIntegrations();
    mapped.bitbucket = legacy.bitbucket || null;

    for (const row of data.integrations || []) {
      const provider = String(row?.provider || '').toLowerCase();
      const normalized = normalizeBackendIntegration(row);
      if (provider === 'github') mapped.github = normalized;
      if (provider === 'gitlab') mapped.gitlab = normalized;
    }

    return {
      github: mapped.github || legacy.github || null,
      gitlab: mapped.gitlab || legacy.gitlab || null,
      bitbucket: mapped.bitbucket || null,
    };
  } catch {
    return legacy;
  }
}

/**
 * Save GitHub token for the current user using backend encrypted storage.
 */
export async function saveGitHubIntegration(token) {
  try {
    const res = await fetch('/api/integrations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'github', token }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: integrationError(data, 'GitHub connection failed') };
    await cleanupLegacyIntegration('github_integration');
    return { ok: true, username: data.username };
  } catch {
    return { ok: false, error: 'Failed to reach GitHub integration service' };
  }
}

/**
 * Disconnect GitHub for the current user.
 */
export async function disconnectGitHub() {
  try {
    const res = await fetch('/api/integrations/github', { method: 'DELETE' });
    await cleanupLegacyIntegration('github_integration');
    return res.ok || res.status === 404;
  } catch {
    await cleanupLegacyIntegration('github_integration');
    return false;
  }
}

/**
 * Save GitLab token + host using backend encrypted storage.
 */
export async function saveGitLabIntegration(token, host) {
  const cleanHost = (host || 'https://gitlab.com').replace(/\/+$/, '');
  try {
    const res = await fetch('/api/integrations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'gitlab', token, gitlabUrl: cleanHost }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: integrationError(data, 'GitLab connection failed') };
    await cleanupLegacyIntegration('gitlab_integration');
    return { ok: true, username: data.username };
  } catch {
    return { ok: false, error: `Failed to reach GitLab integration service at ${cleanHost}` };
  }
}

/**
 * Disconnect GitLab for the current user.
 */
export async function disconnectGitLab() {
  try {
    const res = await fetch('/api/integrations/gitlab', { method: 'DELETE' });
    await cleanupLegacyIntegration('gitlab_integration');
    return res.ok || res.status === 404;
  } catch {
    await cleanupLegacyIntegration('gitlab_integration');
    return false;
  }
}

// ─────────────────────────────────────────────────
//  Fetch Repos
// ─────────────────────────────────────────────────

function normalizeGitHubRepo(r) {
  const fullName = r.fullName || r.full_name || r.name || '';
  return {
    id: r.id || fullName,
    name: fullName,
    fullName,
    description: r.description || '',
    language: r.language || '',
    stars: r.stars ?? r.stargazers_count ?? 0,
    updated: r.updated || r.updated_at || '',
    private: !!r.private,
    provider: 'github',
    defaultBranch: r.defaultBranch || r.default_branch || 'main',
    url: r.url || r.html_url || (fullName ? `https://github.com/${fullName}` : ''),
    cloneUrl: r.cloneUrl || r.clone_url || '',
  };
}

function normalizeGitLabRepo(r, host = 'https://gitlab.com') {
  const providerHost = (r.providerHost || r.gitlabUrl || host || 'https://gitlab.com').replace(/\/+$/, '');
  const fullName = r.fullName || r.path_with_namespace || r.name || '';
  return {
    id: r.id || fullName,
    name: fullName,
    fullName,
    description: r.description || '',
    language: r.language || '',
    stars: r.stars ?? r.star_count ?? 0,
    updated: r.updated || r.last_activity_at || '',
    private: !!r.private || r.visibility === 'private',
    provider: 'gitlab',
    providerHost,
    host: providerHost,
    defaultBranch: r.defaultBranch || r.default_branch || 'main',
    url: r.url || r.web_url || (fullName ? `${providerHost}/${fullName}` : ''),
    cloneUrl: r.cloneUrl || r.http_url_to_repo || '',
  };
}

export async function fetchGitHubRepos(token) {
  if (token) {
    try {
      const res = await fetch('https://api.github.com/user/repos?per_page=50&sort=updated&affiliation=owner,collaborator,organization_member', {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
        },
      });
      if (!res.ok) return [];
      const repos = await res.json();
      return repos.map(normalizeGitHubRepo);
    } catch {
      return [];
    }
  }

  try {
    const res = await fetch('/api/git-repos/github', { cache: 'no-store' });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.repos || []).map(normalizeGitHubRepo);
  } catch {
    return [];
  }
}

export async function fetchGitHubBranches(token, repoFullName) {
  if (!repoFullName) return [];
  if (token) {
    try {
      const res = await fetch(`https://api.github.com/repos/${repoFullName}/branches?per_page=100`, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
        },
      });
      if (!res.ok) return [];
      const branches = await res.json();
      return branches.map(b => b.name);
    } catch {
      return [];
    }
  }

  const [owner, repo] = String(repoFullName).split('/');
  if (!owner || !repo) return [];
  try {
    const qs = new URLSearchParams({ owner, repo });
    const res = await fetch(`/api/git-branches/github?${qs.toString()}`, { cache: 'no-store' });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.branches || []).map(b => b.name || b);
  } catch {
    return [];
  }
}

export async function fetchGitLabRepos(host, token) {
  const baseUrl = (host || 'https://gitlab.com').replace(/\/+$/, '');
  if (token) {
    try {
      const res = await fetch(`${baseUrl}/api/v4/projects?membership=true&per_page=50&order_by=last_activity_at`, {
        headers: { 'PRIVATE-TOKEN': token },
      });
      if (!res.ok) return [];
      const repos = await res.json();
      return repos.map(r => normalizeGitLabRepo(r, baseUrl));
    } catch {
      return [];
    }
  }

  try {
    const res = await fetch('/api/git-repos/gitlab', { cache: 'no-store' });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.repos || []).map(r => normalizeGitLabRepo(r, baseUrl));
  } catch {
    return [];
  }
}

export async function fetchGitLabBranches(host, token, projectId) {
  if (!projectId) return [];
  const baseUrl = (host || 'https://gitlab.com').replace(/\/+$/, '');
  if (token) {
    try {
      const res = await fetch(`${baseUrl}/api/v4/projects/${encodeURIComponent(projectId)}/repository/branches?per_page=100`, {
        headers: { 'PRIVATE-TOKEN': token },
      });
      if (!res.ok) return [];
      const branches = await res.json();
      return branches.map(b => b.name);
    } catch {
      return [];
    }
  }

  try {
    const qs = new URLSearchParams({ projectId: String(projectId) });
    const res = await fetch(`/api/git-branches/gitlab?${qs.toString()}`, { cache: 'no-store' });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.branches || []).map(b => b.name || b);
  } catch {
    return [];
  }
}

// ─────────────────────────────────────────────────
//  Bitbucket Integration
// ─────────────────────────────────────────────────

/**
 * Save Bitbucket app password. Requires repository:write scope.
 * @param {string} username — Bitbucket username
 * @param {string} appPassword — App password (not OAuth token)
 */
export async function saveBitbucketIntegration(username, appPassword) {
  const supabase = getSupabaseBrowserClient();

  const valid = await validateBitbucketToken(username, appPassword);
  if (!valid.ok) return { ok: false, error: valid.error };

  const { error } = await supabase.auth.updateUser({
    data: {
      bitbucket_integration: {
        token: appPassword,
        username,
        connected: true,
        displayName: valid.displayName,
        avatar: valid.avatar,
        connectedAt: new Date().toISOString(),
      },
    },
  });

  return error ? { ok: false, error: error.message } : { ok: true, username };
}

export async function disconnectBitbucket() {
  const supabase = getSupabaseBrowserClient();
  const { error } = await supabase.auth.updateUser({
    data: { bitbucket_integration: null },
  });
  return !error;
}

async function validateBitbucketToken(username, appPassword) {
  try {
    const res = await fetch('https://api.bitbucket.org/2.0/user', {
      headers: {
        Authorization: `Basic ${btoa(`${username}:${appPassword}`)}`,
      },
    });
    if (!res.ok) return { ok: false, error: 'Invalid username or app password' };
    const data = await res.json();
    return {
      ok: true,
      displayName: data.display_name,
      avatar: data.links?.avatar?.href || '',
    };
  } catch {
    return { ok: false, error: 'Failed to reach Bitbucket API' };
  }
}

export async function fetchBitbucketRepos(username, appPassword) {
  if (!username || !appPassword) return [];
  try {
    const res = await fetch(
      `https://api.bitbucket.org/2.0/repositories/${username}?pagelen=50&sort=-updated_on`,
      {
        headers: {
          Authorization: `Basic ${btoa(`${username}:${appPassword}`)}`,
        },
      }
    );
    if (!res.ok) return [];
    const data = await res.json();
    return (data.values || []).map(r => ({
      id: r.uuid,
      name: r.full_name,
      description: r.description || '',
      language: r.language || '',
      stars: 0,
      updated: r.updated_on,
      private: r.is_private,
      provider: 'bitbucket',
      defaultBranch: r.mainbranch?.name || 'main',
      url: r.links?.html?.href || '',
    }));
  } catch {
    return [];
  }
}
