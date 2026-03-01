'use client';

import {
  Github, ChevronDown, Check,
  User, Mail, Loader2, ExternalLink,
  Unlink, Eye, EyeOff, RefreshCw, AlertCircle
} from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';
import {
  getIntegrations,
  saveGitHubIntegration,
  disconnectGitHub,
  saveGitLabIntegration,
  disconnectGitLab,
  fetchGitHubRepos,
  fetchGitLabRepos,
} from '@/lib/integrations';
import Toast from '@/components/Toast';

/* GitLab SVG icon */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

/* ════════════════════════════════════════════════
   GITHUB CARD — Token-based
   ════════════════════════════════════════════════ */
function GitHubCard({ integration, onRefresh, onToast }) {
  const [expanded, setExpanded] = useState(false);
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState('');
  const [repoCount, setRepoCount] = useState(null);
  const [testingConnection, setTestingConnection] = useState(false);

  const connected = integration?.connected;

  const testConnection = useCallback(async () => {
    if (!integration?.token) return;
    setTestingConnection(true);
    const repos = await fetchGitHubRepos(integration.token);
    setRepoCount(repos.length);
    setTestingConnection(false);
  }, [integration?.token]);

  useEffect(() => {
    if (connected && expanded && repoCount === null) {
      testConnection();
    }
  }, [connected, expanded, repoCount, testConnection]);

  const handleSave = async () => {
    if (!token.trim()) return;
    setSaving(true);
    setError('');
    const result = await saveGitHubIntegration(token.trim());
    if (result.ok) {
      setToken('');
      onRefresh();
      onToast('GitHub connected successfully!');
    } else {
      setError(result.error);
    }
    setSaving(false);
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    await disconnectGitHub();
    setRepoCount(null);
    onRefresh();
    onToast('GitHub disconnected.');
    setDisconnecting(false);
  };

  return (
    <div className={cn(
      "bg-white dark:bg-[#151b23] rounded-2xl border overflow-hidden transition-all duration-300",
      connected ? "border-emerald-200 dark:border-emerald-500/20" : "border-slate-200 dark:border-slate-700/50",
      expanded && "shadow-lg dark:shadow-black/20"
    )}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/50 dark:hover:bg-white/[0.02] transition-all"
      >
        <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-white/[0.06] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center shrink-0">
          <Github className="w-6 h-6 text-slate-900 dark:text-slate-100" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">GitHub</h3>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={cn("w-1.5 h-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")} />
            <span className={cn("text-xs font-medium", connected ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400 dark:text-slate-500")}>
              {connected ? `Connected as ${integration.username}` : 'Not connected'}
            </span>
          </div>
        </div>
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 shrink-0 transition-transform",
          expanded && "rotate-180"
        )}>
          <ChevronDown className="w-4 h-4 text-slate-400" />
        </div>
      </button>

      <div className={cn(
        "overflow-hidden transition-all duration-300 ease-in-out",
        expanded ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"
      )}>
        <div className="px-5 pb-5 pt-3 border-t border-slate-100 dark:border-slate-700/30 space-y-4">
          {connected ? (
            <>
              {/* Connected State */}
              <div className="flex items-center gap-4 p-4 bg-slate-50 dark:bg-white/[0.03] rounded-xl border border-slate-100 dark:border-slate-700/30">
                {integration.avatar && (
                  <img src={integration.avatar} alt="" className="w-10 h-10 rounded-full" />
                )}
                <div className="flex-1">
                  <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{integration.username}</p>
                  <p className="text-xs text-slate-400 dark:text-slate-500">github.com</p>
                </div>
                <span className="px-2.5 py-1 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 rounded-lg text-[10px] font-bold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">
                  Connected
                </span>
              </div>

              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500 dark:text-slate-400">Accessible repositories</span>
                <div className="flex items-center gap-2">
                  {testingConnection ? (
                    <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin" />
                  ) : (
                    <span className="font-bold text-slate-900 dark:text-slate-100">{repoCount ?? '—'}</span>
                  )}
                  <button onClick={testConnection} className="p-1 text-slate-400 hover:text-blue-500 transition-colors">
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-700/30">
                <button
                  onClick={handleDisconnect}
                  disabled={disconnecting}
                  className="flex items-center gap-2 px-4 py-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-xl text-sm font-bold transition-colors"
                >
                  {disconnecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlink className="w-4 h-4" />}
                  Disconnect
                </button>
                <a
                  href={`https://github.com/${integration.username}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm text-blue-600 dark:text-blue-400 font-medium hover:underline"
                >
                  View Profile <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            </>
          ) : (
            <>
              {/* Token Input */}
              <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                Enter your GitHub Personal Access Token to connect. We&apos;ll validate it automatically.
              </p>

              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Personal Access Token</label>
                <div className="relative">
                  <input
                    type={showToken ? 'text' : 'password'}
                    value={token}
                    onChange={(e) => { setToken(e.target.value); setError(''); }}
                    placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                    className="w-full px-4 py-2.5 pr-10 bg-white dark:bg-[#0d1117] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken(!showToken)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                  >
                    {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-2">
                  Create at{' '}
                  <a href="https://github.com/settings/tokens/new" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 hover:underline">
                    github.com/settings/tokens
                  </a>
                  {' '}with <code className="text-[10px] px-1 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded font-mono">repo</code> scope.
                </p>
              </div>

              {error && (
                <div className="flex items-center gap-2 text-red-500 text-sm">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  {error}
                </div>
              )}

              <button
                onClick={handleSave}
                disabled={!token.trim() || saving}
                className={cn(
                  "w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-bold transition-all",
                  token.trim() && !saving
                    ? "bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100"
                    : "bg-slate-100 dark:bg-white/[0.06] text-slate-400 cursor-not-allowed"
                )}
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                {saving ? 'Validating & Saving...' : 'Connect GitHub'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════
   GITLAB CARD — Token + Host URL
   ════════════════════════════════════════════════ */
function GitLabCard({ integration, onRefresh, onToast }) {
  const [expanded, setExpanded] = useState(false);
  const [token, setToken] = useState('');
  const [host, setHost] = useState('https://gitlab.com');
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState('');
  const [repoCount, setRepoCount] = useState(null);
  const [testingConnection, setTestingConnection] = useState(false);

  const connected = integration?.connected;

  const testConnection = useCallback(async () => {
    if (!integration?.token || !integration?.host) return;
    setTestingConnection(true);
    const repos = await fetchGitLabRepos(integration.host, integration.token);
    setRepoCount(repos.length);
    setTestingConnection(false);
  }, [integration?.token, integration?.host]);

  useEffect(() => {
    if (connected && expanded && repoCount === null) {
      testConnection();
    }
  }, [connected, expanded, repoCount, testConnection]);

  const handleSave = async () => {
    if (!token.trim()) return;
    setSaving(true);
    setError('');
    const result = await saveGitLabIntegration(token.trim(), host.trim());
    if (result.ok) {
      setToken('');
      setHost('https://gitlab.com');
      onRefresh();
      onToast('GitLab connected successfully!');
    } else {
      setError(result.error);
    }
    setSaving(false);
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    await disconnectGitLab();
    setRepoCount(null);
    onRefresh();
    onToast('GitLab disconnected.');
    setDisconnecting(false);
  };

  return (
    <div className={cn(
      "bg-white dark:bg-[#151b23] rounded-2xl border overflow-hidden transition-all duration-300",
      connected ? "border-emerald-200 dark:border-emerald-500/20" : "border-slate-200 dark:border-slate-700/50",
      expanded && "shadow-lg dark:shadow-black/20"
    )}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/50 dark:hover:bg-white/[0.02] transition-all"
      >
        <div className="w-10 h-10 rounded-xl bg-orange-50 dark:bg-orange-500/10 border border-orange-100 dark:border-orange-500/20 flex items-center justify-center shrink-0">
          <GitLabIcon className="w-6 h-6 text-orange-500" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">GitLab</h3>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={cn("w-1.5 h-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")} />
            <span className={cn("text-xs font-medium", connected ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400 dark:text-slate-500")}>
              {connected ? `Connected as ${integration.username} · ${integration.host}` : 'Not connected'}
            </span>
          </div>
        </div>
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 shrink-0 transition-transform",
          expanded && "rotate-180"
        )}>
          <ChevronDown className="w-4 h-4 text-slate-400" />
        </div>
      </button>

      <div className={cn(
        "overflow-hidden transition-all duration-300 ease-in-out",
        expanded ? "max-h-[600px] opacity-100" : "max-h-0 opacity-0"
      )}>
        <div className="px-5 pb-5 pt-3 border-t border-slate-100 dark:border-slate-700/30 space-y-4">
          {connected ? (
            <>
              <div className="flex items-center gap-4 p-4 bg-slate-50 dark:bg-white/[0.03] rounded-xl border border-slate-100 dark:border-slate-700/30">
                {integration.avatar && (
                  <img src={integration.avatar} alt="" className="w-10 h-10 rounded-full" />
                )}
                <div className="flex-1">
                  <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{integration.username}</p>
                  <p className="text-xs text-slate-400 dark:text-slate-500 truncate">{integration.host}</p>
                </div>
                <span className="px-2.5 py-1 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 rounded-lg text-[10px] font-bold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">
                  Connected
                </span>
              </div>

              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500 dark:text-slate-400">Accessible repositories</span>
                <div className="flex items-center gap-2">
                  {testingConnection ? (
                    <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin" />
                  ) : (
                    <span className="font-bold text-slate-900 dark:text-slate-100">{repoCount ?? '—'}</span>
                  )}
                  <button onClick={testConnection} className="p-1 text-slate-400 hover:text-blue-500 transition-colors">
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-700/30">
                <button
                  onClick={handleDisconnect}
                  disabled={disconnecting}
                  className="flex items-center gap-2 px-4 py-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-xl text-sm font-bold transition-colors"
                >
                  {disconnecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlink className="w-4 h-4" />}
                  Disconnect
                </button>
                <a
                  href={`${integration.host}/${integration.username}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm text-blue-600 dark:text-blue-400 font-medium hover:underline"
                >
                  View Profile <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                Enter your GitLab Personal Access Token and host URL. Works with gitlab.com and self-hosted instances.
              </p>

              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">GitLab Host URL</label>
                <input
                  type="url"
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  placeholder="https://gitlab.com"
                  className="w-full px-4 py-2.5 bg-white dark:bg-[#0d1117] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">
                  Use <code className="text-[10px] px-1 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded font-mono">https://gitlab.com</code> or your self-hosted URL
                </p>
              </div>

              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Personal Access Token</label>
                <div className="relative">
                  <input
                    type={showToken ? 'text' : 'password'}
                    value={token}
                    onChange={(e) => { setToken(e.target.value); setError(''); }}
                    placeholder="glpat-xxxxxxxxxxxxxxxxxxxx"
                    className="w-full px-4 py-2.5 pr-10 bg-white dark:bg-[#0d1117] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken(!showToken)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                  >
                    {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-2">
                  Create at <strong>Settings → Access Tokens</strong> with <code className="text-[10px] px-1 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded font-mono">api</code> + <code className="text-[10px] px-1 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded font-mono">read_repository</code> scopes.
                </p>
              </div>

              {error && (
                <div className="flex items-center gap-2 text-red-500 text-sm">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  {error}
                </div>
              )}

              <button
                onClick={handleSave}
                disabled={!token.trim() || saving}
                className={cn(
                  "w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-bold transition-all",
                  token.trim() && !saving
                    ? "bg-orange-500 text-white hover:bg-orange-600"
                    : "bg-slate-100 dark:bg-white/[0.06] text-slate-400 cursor-not-allowed"
                )}
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                {saving ? 'Validating & Saving...' : 'Connect GitLab'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════
   MAIN INTEGRATIONS PAGE
   ════════════════════════════════════════════════ */
export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState({ github: null, gitlab: null });
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const [gitUsername, setGitUsername] = useState('');
  const [gitEmail, setGitEmail] = useState('');

  const loadIntegrations = useCallback(async () => {
    const data = await getIntegrations();
    setIntegrations(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadIntegrations();
  }, [loadIntegrations]);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (user) {
        setGitUsername(user.user_metadata?.git_username || user.user_metadata?.name || '');
        setGitEmail(user.user_metadata?.git_email || user.email || '');
      }
    });
  }, []);

  const saveGitSettings = async () => {
    const supabase = getSupabaseBrowserClient();
    await supabase.auth.updateUser({
      data: { git_username: gitUsername, git_email: gitEmail },
    });
    setToast({ message: 'Git settings saved!', type: 'success' });
  };

  if (loading) {
    return (
      <div className="min-h-full bg-[#f5f7fa] dark:bg-[#0d1117] flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-full bg-[#f5f7fa] dark:bg-[#0d1117] transition-colors duration-200">
      {toast && <Toast message={toast.message} type={toast.type} onDone={() => setToast(null)} />}

      <div className="px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight mb-1">
            Integrations
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Connect your Git providers to access repositories and push code changes.
          </p>
        </div>

        <div className="space-y-3 mb-8">
          <GitHubCard
            integration={integrations.github}
            onRefresh={loadIntegrations}
            onToast={(msg) => setToast({ message: msg, type: 'success' })}
          />
          <GitLabCard
            integration={integrations.gitlab}
            onRefresh={loadIntegrations}
            onToast={(msg) => setToast({ message: msg, type: 'success' })}
          />
        </div>

        <div className="bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 p-6">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-1">Git Settings</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-5">Configure the username and email that Lucid AI uses to commit changes.</p>

          <div className="space-y-4">
            <div>
              <label className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5 block">Username</label>
              <div className="relative">
                <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  value={gitUsername}
                  onChange={(e) => setGitUsername(e.target.value)}
                  placeholder="AI Engineer"
                  className="w-full pl-10 pr-4 py-2.5 bg-slate-50 dark:bg-[#0d1117] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
            </div>
            <div>
              <label className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5 block">Email</label>
              <div className="relative">
                <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  value={gitEmail}
                  onChange={(e) => setGitEmail(e.target.value)}
                  placeholder="ai@lucid.ai"
                  className="w-full pl-10 pr-4 py-2.5 bg-slate-50 dark:bg-[#0d1117] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
            </div>
            <button
              onClick={saveGitSettings}
              className="px-5 py-2 bg-blue-600 text-white rounded-xl text-xs font-bold hover:bg-blue-700 transition-colors shadow-sm shadow-blue-600/15"
            >
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
