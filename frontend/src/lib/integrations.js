// ─────────────────────────────────────────────────────────
//  Lucid AI — Integration helpers
//  Token-based GitHub & GitLab connections per user
//  Stored in Supabase user_metadata
// ─────────────────────────────────────────────────────────

import { getSupabaseBrowserClient } from '@/lib/supabase/client';

/**
 * Read integration data from the current user's metadata.
 */
export async function getIntegrations() {
  const supabase = getSupabaseBrowserClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) return { github: null, gitlab: null };

  const meta = user.user_metadata || {};

  return {
    github: meta.github_integration || null,
    // { token, connected }
    gitlab: meta.gitlab_integration || null,
    // { token, host, connected }
  };
}

/**
 * Save GitHub token for the current user.
 */
export async function saveGitHubIntegration(token) {
  const supabase = getSupabaseBrowserClient();

  // Validate first
  const valid = await validateGitHubToken(token);
  if (!valid.ok) return { ok: false, error: valid.error };

  const { error } = await supabase.auth.updateUser({
    data: {
      github_integration: {
        token,
        connected: true,
        username: valid.username,
        avatar: valid.avatar,
        connectedAt: new Date().toISOString(),
      },
    },
  });

  return error ? { ok: false, error: error.message } : { ok: true, username: valid.username };
}

/**
 * Disconnect GitHub for the current user.
 */
export async function disconnectGitHub() {
  const supabase = getSupabaseBrowserClient();
  const { error } = await supabase.auth.updateUser({
    data: { github_integration: null },
  });
  return !error;
}

/**
 * Save GitLab token + host for the current user.
 */
export async function saveGitLabIntegration(token, host) {
  const supabase = getSupabaseBrowserClient();
  const cleanHost = (host || 'https://gitlab.com').replace(/\/+$/, '');

  // Validate first
  const valid = await validateGitLabToken(token, cleanHost);
  if (!valid.ok) return { ok: false, error: valid.error };

  const { error } = await supabase.auth.updateUser({
    data: {
      gitlab_integration: {
        token,
        host: cleanHost,
        connected: true,
        username: valid.username,
        avatar: valid.avatar,
        connectedAt: new Date().toISOString(),
      },
    },
  });

  return error ? { ok: false, error: error.message } : { ok: true, username: valid.username };
}

/**
 * Disconnect GitLab for the current user.
 */
export async function disconnectGitLab() {
  const supabase = getSupabaseBrowserClient();
  const { error } = await supabase.auth.updateUser({
    data: { gitlab_integration: null },
  });
  return !error;
}

// ─────────────────────────────────────────────────
//  Validation
// ─────────────────────────────────────────────────

async function validateGitHubToken(token) {
  try {
    const res = await fetch('https://api.github.com/user', {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
      },
    });
    if (!res.ok) return { ok: false, error: 'Invalid token or insufficient permissions' };
    const data = await res.json();
    return { ok: true, username: data.login, avatar: data.avatar_url };
  } catch {
    return { ok: false, error: 'Failed to reach GitHub API' };
  }
}

async function validateGitLabToken(token, host) {
  try {
    const res = await fetch(`${host}/api/v4/user`, {
      headers: { 'PRIVATE-TOKEN': token },
    });
    if (!res.ok) return { ok: false, error: 'Invalid token or insufficient permissions' };
    const data = await res.json();
    return { ok: true, username: data.username, avatar: data.avatar_url };
  } catch {
    return { ok: false, error: `Failed to reach GitLab API at ${host}` };
  }
}

// ─────────────────────────────────────────────────
//  Fetch Repos
// ─────────────────────────────────────────────────

export async function fetchGitHubRepos(token) {
  if (!token) return [];
  try {
    const res = await fetch('https://api.github.com/user/repos?per_page=50&sort=updated&affiliation=owner,collaborator,organization_member', {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
      },
    });
    if (!res.ok) return [];
    const repos = await res.json();
    return repos.map(r => ({
      id: r.id,
      name: r.full_name,
      description: r.description || '',
      language: r.language || '',
      stars: r.stargazers_count,
      updated: r.updated_at,
      private: r.private,
      provider: 'github',
      defaultBranch: r.default_branch,
      url: r.html_url,
    }));
  } catch {
    return [];
  }
}

export async function fetchGitHubBranches(token, repoFullName) {
  if (!token || !repoFullName) return [];
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

export async function fetchGitLabRepos(host, token) {
  if (!token || !host) return [];
  try {
    const baseUrl = host.replace(/\/+$/, '');
    const res = await fetch(`${baseUrl}/api/v4/projects?membership=true&per_page=50&order_by=updated_at`, {
      headers: { 'PRIVATE-TOKEN': token },
    });
    if (!res.ok) return [];
    const repos = await res.json();
    return repos.map(r => ({
      id: r.id,
      name: r.path_with_namespace,
      description: r.description || '',
      language: '',
      stars: r.star_count,
      updated: r.last_activity_at,
      private: r.visibility === 'private',
      provider: 'gitlab',
      providerHost: host,
      defaultBranch: r.default_branch,
      url: r.web_url,
    }));
  } catch {
    return [];
  }
}

export async function fetchGitLabBranches(host, token, projectId) {
  if (!token || !host || !projectId) return [];
  try {
    const baseUrl = host.replace(/\/+$/, '');
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
