'use client';

import {
  Github, Check, User, Mail, Loader2, ExternalLink,
  Unlink, Eye, EyeOff, RefreshCw, AlertCircle, X,
  Plus, Settings2, Shield, Globe, Lock, Zap,
  GitBranch, Database, Activity, ArrowUpRight
} from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
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

/* ─── GitLab Icon ─── */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

/* ════════════════════════════════════════════════════════
   INTEGRATION MODAL
════════════════════════════════════════════════════════ */
function IntegrationModal({ isOpen, onClose, type, integration, onRefresh, onToast }) {
  const [token, setToken] = useState('');
  const [host, setHost] = useState('https://gitlab.com');
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState('');
  const [repoCount, setRepoCount] = useState(null);
  const [testingConnection, setTestingConnection] = useState(false);

  const isGitHub = type === 'github';
  const isGitLab = type === 'gitlab';
  const connected = integration?.connected;

  useEffect(() => {
    if (isOpen) {
      setError('');
      setToken('');
      setRepoCount(null);
      if (isGitLab) setHost(integration?.host || 'https://gitlab.com');
    }
  }, [isOpen, type]);

  useEffect(() => {
    if (connected && isOpen) testConnection();
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps


  const testConnection = useCallback(async () => {
    if (!integration?.token) return;
    setTestingConnection(true);
    let count = 0;
    if (isGitHub) {
      const repos = await fetchGitHubRepos(integration.token);
      count = repos.length;
    } else if (isGitLab) {
      const repos = await fetchGitLabRepos(integration.host, integration.token);
      count = repos.length;
    }
    setRepoCount(count);
    setTestingConnection(false);
  }, [integration, isGitHub, isGitLab]);

  const handleSave = async () => {
    if (!token.trim()) return;
    setSaving(true);
    setError('');
    const result = isGitHub
      ? await saveGitHubIntegration(token.trim())
      : await saveGitLabIntegration(token.trim(), host.trim());
    if (result.ok) {
      setToken('');
      onRefresh();
      onToast(`${isGitHub ? 'GitHub' : 'GitLab'} connected successfully!`);
      onClose();
    } else {
      setError(result.error);
    }
    setSaving(false);
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    isGitHub ? await disconnectGitHub() : await disconnectGitLab();
    setRepoCount(null);
    onRefresh();
    onToast(`${isGitHub ? 'GitHub' : 'GitLab'} disconnected.`);
    setDisconnecting(false);
    onClose();
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-black/30 dark:bg-black/60 backdrop-blur-sm"
          />

          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 16 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            className={cn(
              'relative w-full max-w-[480px] rounded-3xl overflow-hidden',
              'bg-white dark:bg-[#111620]',
              'border border-slate-200/80 dark:border-white/[0.07]',
              'shadow-2xl shadow-slate-300/30 dark:shadow-black/50'
            )}
          >
            {/* ══ TOP HEADER BAR ══ */}
            <div className="flex items-center justify-between px-6 py-5 border-b border-slate-100 dark:border-white/[0.06]">
              <div className="flex items-center gap-3">
                <div className={cn(
                  'w-10 h-10 rounded-xl flex items-center justify-center border',
                  isGitHub
                    ? 'bg-slate-50 dark:bg-white/[0.05] border-slate-200 dark:border-white/10'
                    : 'bg-orange-50 dark:bg-orange-500/10 border-orange-100 dark:border-orange-500/20'
                )}>
                  {isGitHub
                    ? <Github className="w-5 h-5 text-slate-800 dark:text-white" />
                    : <GitLabIcon className="w-5 h-5 text-orange-500" />}
                </div>
                <div>
                  <h3 className="text-[15px] font-extrabold text-slate-900 dark:text-white leading-tight">
                    {connected ? `Manage ${isGitHub ? 'GitHub' : 'GitLab'}` : `Connect ${isGitHub ? 'GitHub' : 'GitLab'}`}
                  </h3>
                  <p className="text-xs text-slate-400 dark:text-white/35 mt-0.5">
                    Token-based · {connected ? 'Manage or revoke access' : 'Secure PAT-based connection'}
                  </p>
                </div>
              </div>
              <button
                onClick={onClose}
                className="w-8 h-8 flex items-center justify-center rounded-full text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10 transition-all"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {connected ? (
              <>
                {/* ══ PROFILE — centered, gray bg ══ */}
                <div className="bg-slate-50 dark:bg-white/[0.03] px-6 py-8 flex flex-col items-center text-center border-b border-slate-100 dark:border-white/[0.05]">
                  {integration.avatar ? (
                    <img src={integration.avatar} alt="" className="w-20 h-20 rounded-2xl mb-4" />
                  ) : (
                    <div className={cn(
                      'w-20 h-20 rounded-2xl mb-4 flex items-center justify-center',
                      isGitHub ? 'bg-slate-200 dark:bg-white/10' : 'bg-orange-100 dark:bg-orange-500/20'
                    )}>
                      <User className="w-9 h-9 text-slate-400 dark:text-white/30" />
                    </div>
                  )}

                  <div className="flex items-center gap-2.5 mb-1.5">
                    <span className="text-[20px] font-black text-slate-900 dark:text-white">{integration.username}</span>
                    <span className="px-2.5 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30 text-[10px] font-black text-emerald-600 dark:text-emerald-400 uppercase tracking-widest">Active</span>
                  </div>

                  <p className="text-sm text-slate-400 dark:text-white/40 mb-4">
                    {isGitHub ? `https://github.com/${integration.username}` : `${integration.host}/${integration.username}`}
                  </p>

                  <div className="flex items-center gap-2.5 flex-wrap justify-center">
                    <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-indigo-200 dark:border-indigo-500/30 bg-indigo-50 dark:bg-indigo-500/10 text-xs font-bold text-indigo-600 dark:text-indigo-400">
                      <Shield className="w-3.5 h-3.5" /> Verified
                    </span>
                    {integration.connectedAt && (
                      <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-slate-200 dark:border-white/10 bg-white dark:bg-white/[0.03] text-xs font-bold text-slate-500 dark:text-white/40">
                        <Activity className="w-3.5 h-3.5" />
                        Since {new Date(integration.connectedAt).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })}
                      </span>
                    )}
                  </div>
                </div>

                {/* ══ STAT TILES — separated by 1px grid lines ══ */}
                <div className="grid grid-cols-3 divide-x divide-slate-100 dark:divide-white/[0.05] border-b border-slate-100 dark:border-white/[0.05]">
                  <div className="p-5 bg-white dark:bg-[#111620]">
                    <div className="flex items-center justify-between mb-3">
                      <p className="text-[10px] font-bold text-slate-400 dark:text-white/30 uppercase tracking-widest">Repositories</p>
                      <button onClick={testConnection} className="text-slate-300 dark:text-white/20 hover:text-indigo-500 dark:hover:text-indigo-400 transition-colors">
                        <RefreshCw className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    {testingConnection
                      ? <Loader2 className="w-6 h-6 text-slate-300 animate-spin" />
                      : <span className="text-2xl font-black text-slate-900 dark:text-white">{repoCount ?? '—'}</span>}
                  </div>
                  <div className="p-5 bg-white dark:bg-[#111620]">
                    <div className="flex items-center justify-between mb-3">
                      <p className="text-[10px] font-bold text-slate-400 dark:text-white/30 uppercase tracking-widest">Scope</p>
                      <Shield className="w-3.5 h-3.5 text-slate-300 dark:text-white/20" />
                    </div>
                    <span className="text-base font-black text-slate-900 dark:text-white">Full Repo</span>
                  </div>
                  <div className="p-5 bg-white dark:bg-[#111620]">
                    <div className="flex items-center justify-between mb-3">
                      <p className="text-[10px] font-bold text-slate-400 dark:text-white/30 uppercase tracking-widest">Auth</p>
                      <Lock className="w-3.5 h-3.5 text-slate-300 dark:text-white/20" />
                    </div>
                    <span className="text-base font-black text-slate-900 dark:text-white">PAT</span>
                  </div>
                </div>

                {/* ══ FOOTER BUTTONS ══ */}
                <div className="flex gap-3 p-5 bg-white dark:bg-[#111620]">
                  <button
                    onClick={handleDisconnect}
                    disabled={disconnecting}
                    className="flex-1 flex items-center justify-center gap-2 py-3.5 rounded-xl border border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/[0.07] text-red-500 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-500/15 text-sm font-bold transition-all"
                  >
                    {disconnecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlink className="w-4 h-4" />}
                    Revoke Access
                  </button>
                  <a
                    href={isGitHub ? `https://github.com/${integration.username}` : `${integration.host}/${integration.username}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={cn(
                      'flex-1 flex items-center justify-center gap-2 py-3.5 rounded-xl text-sm font-bold text-white transition-all',
                      isGitHub
                        ? 'bg-slate-900 hover:bg-slate-800 shadow-sm shadow-slate-200 dark:shadow-black/20'
                        : 'bg-indigo-600 hover:bg-indigo-700 shadow-sm shadow-indigo-200/40 dark:shadow-indigo-900/20'
                    )}
                  >
                    View Profile <ArrowUpRight className="w-4 h-4" />
                  </a>
                </div>
              </>
            ) : (
              /* ══ CONNECT FORM ══ */
              <div className="px-6 py-6 space-y-4">
                {isGitLab && (
                  <div className="space-y-2">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Instance URL</label>
                    <div className="relative">
                      <Globe className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                      <input
                        type="url"
                        value={host}
                        onChange={(e) => setHost(e.target.value)}
                        placeholder="https://gitlab.com"
                        className="w-full pl-11 pr-4 py-3.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-2xl text-sm text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-4 focus:ring-indigo-500/5 outline-none transition-all"
                      />
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Personal Access Token</label>
                    <a
                      href={isGitHub ? 'https://github.com/settings/tokens/new' : `${host}/profile/personal_access_tokens`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] font-bold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-1 transition-colors"
                    >
                      Generate token <ArrowUpRight className="w-3 h-3" />
                    </a>
                  </div>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                    <input
                      type={showToken ? 'text' : 'password'}
                      value={token}
                      onChange={(e) => { setToken(e.target.value); setError(''); }}
                      placeholder={isGitHub ? 'ghp_••••••••••••••••••' : 'glpat-••••••••••••••••'}
                      className="w-full pl-11 pr-12 py-3.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-2xl text-sm text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 font-mono focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-4 focus:ring-indigo-500/5 outline-none transition-all"
                    />
                    <button
                      type="button"
                      onClick={() => setShowToken(!showToken)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-300 dark:text-white/25 hover:text-slate-500 dark:hover:text-white/60 transition-colors"
                    >
                      {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  <div className="flex items-start gap-2 px-1 pt-0.5">
                    <Shield className="w-3.5 h-3.5 text-slate-300 dark:text-white/20 shrink-0 mt-0.5" />
                    <p className="text-[11px] text-slate-400 dark:text-white/30 leading-relaxed">
                      Required scopes:&nbsp;
                      {isGitHub
                        ? <><code className="text-indigo-600 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-500/[0.15] px-1.5 py-0.5 rounded-md font-mono">repo</code>&nbsp;·&nbsp;<code className="text-indigo-600 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-500/[0.15] px-1.5 py-0.5 rounded-md font-mono">read:user</code></>
                        : <><code className="text-orange-600 dark:text-orange-300 bg-orange-50 dark:bg-orange-500/[0.15] px-1.5 py-0.5 rounded-md font-mono">api</code>&nbsp;·&nbsp;<code className="text-orange-600 dark:text-orange-300 bg-orange-50 dark:bg-orange-500/[0.15] px-1.5 py-0.5 rounded-md font-mono">read_repository</code></>
                      }
                    </p>
                  </div>
                </div>

                <AnimatePresence>
                  {error && (
                    <motion.div
                      initial={{ opacity: 0, y: -6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -6 }}
                      className="flex items-center gap-3 p-4 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-2xl text-red-600 dark:text-red-400 text-sm font-medium"
                    >
                      <AlertCircle className="w-4 h-4 shrink-0" />
                      {error}
                    </motion.div>
                  )}
                </AnimatePresence>

                <div className="pt-1">
                  <button
                    onClick={handleSave}
                    disabled={!token.trim() || saving}
                    className={cn(
                      'w-full flex items-center justify-center gap-2.5 py-4 rounded-2xl text-sm font-bold transition-all',
                      token.trim() && !saving
                        ? 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-lg shadow-indigo-200/50 dark:shadow-indigo-900/30'
                        : 'bg-slate-100 dark:bg-white/[0.05] text-slate-400 dark:text-white/25 cursor-not-allowed'
                    )}
                  >
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                    {saving ? 'Authenticating...' : `Authorize ${isGitHub ? 'GitHub' : 'GitLab'}`}
                  </button>
                </div>
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

/* ════════════════════════════════════════════════════════
   HORIZONTAL INTEGRATION ROW CARD
════════════════════════════════════════════════════════ */
function IntegrationCard({ type, integration, onClick }) {
  const isGitHub = type === 'github';
  const connected = integration?.connected;

  return (
    <div className={cn(
      'flex items-center gap-4 px-5 py-4 rounded-2xl border transition-all duration-200',
      'bg-white dark:bg-[#111620]',
      connected
        ? 'border-slate-200 dark:border-white/[0.08] shadow-sm shadow-slate-100 dark:shadow-none'
        : 'border-slate-200 dark:border-white/[0.07] shadow-sm shadow-slate-100 dark:shadow-none'
    )}>
      {/* Icon */}
      <div className={cn(
        'w-11 h-11 rounded-xl flex items-center justify-center border shrink-0',
        isGitHub
          ? 'bg-slate-50 dark:bg-white/5 border-slate-200 dark:border-white/10'
          : 'bg-orange-50 dark:bg-orange-500/10 border-orange-100 dark:border-orange-500/20'
      )}>
        {isGitHub
          ? <Github className="w-6 h-6 text-slate-800 dark:text-white" />
          : <GitLabIcon className="w-6 h-6 text-orange-500" />}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-base font-black text-slate-900 dark:text-white tracking-tight">
            {isGitHub ? 'GitHub' : 'GitLab'}
          </span>
          {connected ? (
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shrink-0" />
              <span className="text-[10px] font-black text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Live</span>
            </span>
          ) : (
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-white/[0.03]">
              <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-white/20 shrink-0" />
              <span className="text-[10px] font-black text-slate-400 dark:text-white/30 uppercase tracking-wider">Inactive</span>
            </span>
          )}
        </div>

        {connected ? (
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm text-slate-500 dark:text-white/40">
              Auth as <span className="font-bold text-slate-700 dark:text-white/70">{integration.username}</span>
            </span>
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-50 dark:bg-white/[0.04] border border-slate-100 dark:border-white/[0.06] text-[11px] font-semibold text-slate-500 dark:text-white/40">
                <GitBranch className="w-3 h-3" /> Repo access
              </span>
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-50 dark:bg-white/[0.04] border border-slate-100 dark:border-white/[0.06] text-[11px] font-semibold text-slate-500 dark:text-white/40">
                <Database className="w-3 h-3" /> Push &amp; Pull
              </span>
            </div>
          </div>
        ) : (
          <p className="text-sm text-slate-400 dark:text-white/30">
            {isGitHub ? 'Connect your GitHub account' : 'Connect your GitLab account'}
          </p>
        )}
      </div>

      {/* Manage / Connect button */}
      <button
        onClick={onClick}
        className={cn(
          'shrink-0 flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-bold transition-all',
          connected
            ? 'border-emerald-200 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 hover:bg-emerald-100 dark:hover:bg-emerald-500/20 hover:border-emerald-300 dark:hover:border-emerald-500/50'
            : 'border-slate-200 dark:border-white/10 text-slate-600 dark:text-white/50 bg-slate-50 dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] hover:text-slate-800 dark:hover:text-white'
        )}
      >
        <Settings2 className="w-4 h-4" />
        {connected ? 'Manage' : 'Connect'}
      </button>
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   MAIN PAGE
════════════════════════════════════════════════════════ */
export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState({ github: null, gitlab: null });
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const [gitUsername, setGitUsername] = useState('');
  const [gitEmail, setGitEmail] = useState('');
  const [modalState, setModalState] = useState({ isOpen: false, type: null });
  const [savingGit, setSavingGit] = useState(false);

  const loadIntegrations = useCallback(async () => {
    const data = await getIntegrations();
    setIntegrations(data);
    setLoading(false);
  }, []);

  useEffect(() => { loadIntegrations(); }, [loadIntegrations]);

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
    setSavingGit(true);
    const supabase = getSupabaseBrowserClient();
    await supabase.auth.updateUser({ data: { git_username: gitUsername, git_email: gitEmail } });
    setToast({ message: 'Git identity saved!', type: 'success' });
    setSavingGit(false);
  };

  const connectedCount = [integrations.github, integrations.gitlab].filter(i => i?.connected).length;

  if (loading) {
    return (
      <div className="min-h-full bg-white dark:bg-[#0d1117] flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-full border-[3px] border-slate-200 dark:border-indigo-500/20 border-t-indigo-500 animate-spin" />
          <p className="text-xs font-bold text-slate-400 dark:text-white/20 uppercase tracking-[0.25em]">Loading</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-full bg-white dark:bg-[#0d1117] transition-colors duration-200">
      {toast && <Toast message={toast.message} type={toast.type} onDone={() => setToast(null)} />}

      <IntegrationModal
        isOpen={modalState.isOpen}
        onClose={() => setModalState({ isOpen: false, type: null })}
        type={modalState.type}
        integration={integrations[modalState.type]}
        onRefresh={loadIntegrations}
        onToast={(msg) => setToast({ message: msg, type: 'success' })}
      />

      <div className="px-8 py-10">

        {/* ══ Header ══ */}
        <div className="flex items-start justify-between mb-10">
          <div>
            {/* Eyebrow */}
            <div className="flex items-center gap-1.5 mb-4">
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-slate-200 dark:border-white/[0.08] bg-white dark:bg-white/[0.03]">
                <Activity className="w-3 h-3 text-indigo-500 dark:text-indigo-400" />
                <span className="text-[10px] font-bold text-slate-500 dark:text-white/50 uppercase tracking-widest">Control Panel</span>
              </div>
            </div>
            <h1 className="text-[2.2rem] font-black text-slate-900 dark:text-white tracking-tight leading-none mb-3">
              Integrations
            </h1>
            <p className="text-sm text-slate-500 dark:text-white/40 font-medium max-w-[380px] leading-relaxed">
              Authorize Lucid AI to interact with your code hosting platforms. All tokens are encrypted and stored securely.
            </p>
          </div>

          {/* Status widget */}
          <div className="text-right shrink-0">
            <div className="flex items-center gap-2 justify-end mb-1">
              <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_5px_2px_rgba(52,211,153,0.3)]" />
              <span className="text-[11px] font-black text-slate-600 dark:text-white/60 uppercase tracking-widest">All Systems Operational</span>
            </div>
            <p className="text-[11px] text-slate-400 dark:text-white/25">
              <span className="text-slate-600 dark:text-white/50 font-bold">{connectedCount}/2</span> services authorized
            </p>
          </div>
        </div>

        {/* ══ Integration Cards — 2 col grid ══ */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          <IntegrationCard
            type="github"
            integration={integrations.github}
            onClick={() => setModalState({ isOpen: true, type: 'github' })}
          />
          <IntegrationCard
            type="gitlab"
            integration={integrations.gitlab}
            onClick={() => setModalState({ isOpen: true, type: 'gitlab' })}
          />
        </div>

        {/* ══ Coming Soon — full width dashed card ══ */}
        <div className={cn(
          'flex items-center gap-4 px-5 py-4 rounded-2xl border border-dashed mb-10',
          'border-slate-300 dark:border-white/[0.07]',
          'bg-white/40 dark:bg-white/[0.01]'
        )}>
          <div className="w-10 h-10 rounded-xl border border-slate-200 dark:border-white/[0.07] bg-white dark:bg-white/[0.02] flex items-center justify-center shrink-0">
            <Plus className="w-4 h-4 text-slate-300 dark:text-white/20" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-slate-400 dark:text-white/25">More Integrations</p>
            <p className="text-xs text-slate-300 dark:text-white/15">Bitbucket, Jira, Slack &amp; more...</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {['Bitbucket', 'Jira', 'Slack'].map((name) => (
              <span
                key={name}
                className="px-2.5 py-1 rounded-lg border border-slate-200 dark:border-white/[0.06] bg-white dark:bg-white/[0.02] text-[10px] font-bold text-slate-300 dark:text-white/20 uppercase tracking-wider"
              >
                {name}
              </span>
            ))}
          </div>
        </div>

        {/* ══ Git Identity ══ */}
        <div className={cn(
          'rounded-2xl border overflow-hidden',
          'bg-white dark:bg-[#111620]',
          'border-slate-200 dark:border-white/[0.07]',
          'shadow-sm shadow-slate-100 dark:shadow-none'
        )}>
          <div className="flex flex-col lg:flex-row">
            {/* Left panel */}
            <div className={cn(
              'p-7 lg:w-[240px] shrink-0',
              'border-b lg:border-b-0 lg:border-r',
              'border-slate-100 dark:border-white/[0.06]'
            )}>
              <div className="w-10 h-10 rounded-xl border border-indigo-100 dark:border-indigo-500/25 bg-indigo-50 dark:bg-indigo-500/10 flex items-center justify-center mb-4">
                <User className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
              </div>
              <h3 className="text-base font-black text-slate-900 dark:text-white tracking-tight mb-2">Git Identity</h3>
              <p className="text-sm text-slate-500 dark:text-white/35 leading-relaxed">
                The author details attached to every commit made by Lucid AI on your behalf.
              </p>
            </div>

            {/* Right panel */}
            <div className="flex-1 p-7">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                {/* Author Name */}
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/30 uppercase tracking-widest">Author Name</label>
                  <div className="relative group">
                    <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/20 group-focus-within:text-indigo-500 dark:group-focus-within:text-indigo-400 transition-colors" />
                    <input
                      value={gitUsername}
                      onChange={(e) => setGitUsername(e.target.value)}
                      placeholder="John Doe"
                      className={cn(
                        'w-full pl-10 pr-4 py-3 rounded-xl text-sm font-medium outline-none transition-all',
                        'bg-slate-50 dark:bg-white/[0.03]',
                        'border border-slate-200 dark:border-white/[0.07]',
                        'text-slate-800 dark:text-white',
                        'placeholder-slate-300 dark:placeholder-white/15',
                        'hover:border-slate-300 dark:hover:border-white/15',
                        'focus:border-indigo-400 dark:focus:border-indigo-500/50',
                        'focus:ring-4 focus:ring-indigo-500/5',
                        'focus:bg-white dark:focus:bg-indigo-500/[0.03]'
                      )}
                    />
                  </div>
                </div>
                {/* Author Email */}
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/30 uppercase tracking-widest">Author Email</label>
                  <div className="relative group">
                    <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/20 group-focus-within:text-indigo-500 dark:group-focus-within:text-indigo-400 transition-colors" />
                    <input
                      value={gitEmail}
                      onChange={(e) => setGitEmail(e.target.value)}
                      placeholder="you@example.com"
                      className={cn(
                        'w-full pl-10 pr-4 py-3 rounded-xl text-sm font-medium outline-none transition-all',
                        'bg-slate-50 dark:bg-white/[0.03]',
                        'border border-slate-200 dark:border-white/[0.07]',
                        'text-slate-800 dark:text-white',
                        'placeholder-slate-300 dark:placeholder-white/15',
                        'hover:border-slate-300 dark:hover:border-white/15',
                        'focus:border-indigo-400 dark:focus:border-indigo-500/50',
                        'focus:ring-4 focus:ring-indigo-500/5',
                        'focus:bg-white dark:focus:bg-indigo-500/[0.03]'
                      )}
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <p className="text-xs italic text-slate-400 dark:text-white/20">Changes apply on the next commit</p>
                <button
                  onClick={saveGitSettings}
                  disabled={savingGit}
                  className="flex items-center gap-2 px-6 py-3 rounded-xl text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 shadow-lg shadow-indigo-200/60 dark:shadow-indigo-900/30 transition-all active:scale-95 disabled:opacity-60"
                >
                  {savingGit ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                  Save Identity
                </button>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
