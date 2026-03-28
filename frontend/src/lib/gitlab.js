// ─────────────────────────────────────────────────────────
//  Lucid AI — GitLab API Service
//  Creates repos, pushes files, sets CI/CD variables,
//  and manages ops folders on a configurable GitLab instance.
// ─────────────────────────────────────────────────────────

/**
 * Create a GitLab API client for the given host.
 *
 * @param {object} opts
 * @param {string} opts.host — e.g. "https://gitlab.udevs.io"
 * @param {string} opts.token — GitLab access token
 */
export function createGitLabClient({ host, token }) {
  const baseUrl = host.replace(/\/+$/, '');
  const api = `${baseUrl}/api/v4`;

  const headers = {
    'Content-Type': 'application/json',
    'PRIVATE-TOKEN': token,
  };

  return {
    // ═════════════════════════════════════════════════
    //  Repository Management
    // ═════════════════════════════════════════════════

    /**
     * Create a new project (repo) under a group/namespace.
     * @param {object} opts
     * @param {string} opts.name — project name
     * @param {number|string} opts.namespaceId — group/namespace ID
     * @param {string} opts.visibility — 'private'|'internal'|'public'
     * @returns {object} — created project data
     */
    async createProject({ name, namespaceId, visibility = 'private' }) {
      const body = {
        name,
        path: name.toLowerCase().replace(/[^a-z0-9-]/g, '-'),
        namespace_id: namespaceId,
        visibility,
        initialize_with_readme: false,
      };

      const res = await fetch(`${api}/projects`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab createProject failed (${res.status}): ${err}`);
      }

      return res.json();
    },

    /**
     * Push multiple files to a repo in a single commit.
     * Uses the Commits API for atomic multi-file commits.
     *
     * @param {object} opts
     * @param {number} opts.projectId — GitLab project ID
     * @param {string} opts.branch — target branch (default "main")
     * @param {string} opts.commitMessage
     * @param {{ path: string, content: string }[]} opts.files
     */
    async pushFiles({ projectId, branch = 'main', commitMessage, files }) {
      // First commit — need to use "create" action and start_branch
      const actions = files.map((f) => ({
        action: 'create',
        file_path: f.path,
        content: f.content,
      }));

      const res = await fetch(`${api}/projects/${projectId}/repository/commits`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          branch,
          start_branch: branch,
          commit_message: commitMessage,
          actions,
        }),
        signal: AbortSignal.timeout(30000),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab pushFiles failed (${res.status}): ${err}`);
      }

      return res.json();
    },

    /**
     * Push files to an EXISTING repo (for ops folder creation).
     * Uses "create" action — files must not already exist.
     */
    async pushFilesToExistingRepo({ projectId, branch = 'main', commitMessage, files }) {
      const actions = files.map((f) => ({
        action: 'create',
        file_path: f.path,
        content: f.content,
      }));

      const res = await fetch(`${api}/projects/${projectId}/repository/commits`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          branch,
          commit_message: commitMessage,
          actions,
        }),
        signal: AbortSignal.timeout(30000),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab pushFilesToExistingRepo failed (${res.status}): ${err}`);
      }

      return res.json();
    },

    // ═════════════════════════════════════════════════
    //  CI/CD Variables
    // ═════════════════════════════════════════════════

    /**
     * Set a CI/CD variable on a project.
     *
     * @param {object} opts
     * @param {number} opts.projectId
     * @param {string} opts.key — variable name
     * @param {string} opts.value — variable value
     * @param {boolean} opts.protected_ — only available on protected branches
     * @param {boolean} opts.masked — mask in logs
     */
    async setVariable({ projectId, key, value, protected_ = false, masked = false }) {
      // Try create first, then update if exists
      const body = {
        key,
        value,
        variable_type: 'env_var',
        protected: protected_,
        masked,
        environment_scope: '*',
      };

      let res = await fetch(`${api}/projects/${projectId}/variables`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(10000),
      });

      // If variable already exists, update it
      if (res.status === 400) {
        res = await fetch(`${api}/projects/${projectId}/variables/${key}`, {
          method: 'PUT',
          headers,
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(10000),
        });
      }

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab setVariable failed (${res.status}): ${err}`);
      }

      return res.json();
    },

    // ═════════════════════════════════════════════════
    //  Project Lookup
    // ═════════════════════════════════════════════════

    /**
     * Find a project by its path (e.g. "ops/deployments").
     */
    async findProjectByPath(path) {
      const encoded = encodeURIComponent(path);
      const res = await fetch(`${api}/projects/${encoded}`, {
        headers,
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) return null;
      return res.json();
    },

    /**
     * List groups/namespaces accessible to the token.
     */
    async listGroups() {
      const res = await fetch(`${api}/groups?per_page=100&min_access_level=30`, {
        headers,
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) return [];
      return res.json();
    },

    // ═════════════════════════════════════════════════
    //  Repo Transfer / Fork for Export
    // ═════════════════════════════════════════════════

    /**
     * Fork a GitLab project to a different namespace.
     * Used for "Export to GitHub" flow — first fork within GitLab,
     * then the user can mirror to GitHub.
     */
    async forkProject({ projectId, targetNamespace }) {
      const res = await fetch(`${api}/projects/${projectId}/fork`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ namespace_path: targetNamespace }),
        signal: AbortSignal.timeout(15000),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab forkProject failed (${res.status}): ${err}`);
      }

      return res.json();
    },

    /**
     * Transfer a project to a different namespace.
     */
    async transferProject({ projectId, targetNamespace }) {
      const res = await fetch(`${api}/projects/${projectId}/transfer`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ namespace: targetNamespace }),
        signal: AbortSignal.timeout(15000),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab transferProject failed (${res.status}): ${err}`);
      }

      return res.json();
    },

    /**
     * Get project details by ID.
     */
    async getProject(projectId) {
      const res = await fetch(`${api}/projects/${projectId}`, {
        headers,
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) return null;
      return res.json();
    },

    // ═════════════════════════════════════════════════
    //  Pipeline Management
    // ═════════════════════════════════════════════════

    /**
     * Trigger a new CI/CD pipeline on a branch.
     *
     * @param {object} opts
     * @param {number} opts.projectId — GitLab project ID
     * @param {string} opts.ref — branch name (default "main")
     * @returns {object} — pipeline data
     */
    async triggerPipeline({ projectId, ref = 'main' }) {
      const res = await fetch(`${api}/projects/${projectId}/pipeline`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ ref }),
        signal: AbortSignal.timeout(15000),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`GitLab triggerPipeline failed (${res.status}): ${err}`);
      }

      return res.json();
    },
  };
}
