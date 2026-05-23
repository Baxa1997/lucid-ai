'use client';

import {
  Github, Check, User, Mail, Loader2, ExternalLink,
  Unlink, Eye, EyeOff, RefreshCw, AlertCircle, X,
  Plus, Settings2, Shield, Globe, Lock, Zap, Search,
  GitBranch, Database, Activity, ArrowUpRight,
  Trash2, Edit3, ServerCrash
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

/* ─── GoDaddy Icon ─── */
function GoDaddyIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
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
    const trimmedToken = token.trim();
    // Client-side sanity checks — reject obviously malformed tokens before
    // calling the provider API so the user gets an actionable error instantly.
    if (!trimmedToken) {
      setError('Please paste a personal access token.');
      return;
    }
    if (/\s/.test(trimmedToken)) {
      setError('Token contains whitespace — check that you copied only the token value.');
      return;
    }
    if (trimmedToken.length < 20) {
      setError('That token looks too short. Generate a new one and paste the full value.');
      return;
    }
    if (isGitHub) {
      const okPrefix = (
        trimmedToken.startsWith('ghp_')
        || trimmedToken.startsWith('github_pat_')
        || trimmedToken.startsWith('gho_')
        || trimmedToken.startsWith('ghs_')
      );
      if (!okPrefix) {
        setError("That doesn't look like a GitHub PAT (expected to start with 'ghp_' or 'github_pat_').");
        return;
      }
    } else {
      const trimmedHost = host.trim();
      if (!trimmedHost || !/^https?:\/\//i.test(trimmedHost)) {
        setError('GitLab host must be a full URL (e.g. https://gitlab.com).');
        return;
      }
      if (!trimmedToken.startsWith('glpat-')) {
        setError("That doesn't look like a GitLab PAT (expected to start with 'glpat-').");
        return;
      }
    }
    setSaving(true);
    setError('');
    const result = isGitHub
      ? await saveGitHubIntegration(trimmedToken)
      : await saveGitLabIntegration(trimmedToken, host.trim());
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
   GODADDY ACCOUNT CARD
════════════════════════════════════════════════════════ */
function GoDaddyAccountCard({ account, onEdit, onDelete, deleting }) {
  return (
    <div className={cn(
      'flex items-center gap-4 px-5 py-4 rounded-2xl border transition-all duration-200',
      'bg-white dark:bg-[#111620]',
      'border-slate-200 dark:border-white/[0.08] shadow-sm shadow-slate-100 dark:shadow-none'
    )}>
      <div className="w-11 h-11 rounded-xl flex items-center justify-center border shrink-0 bg-teal-50 dark:bg-teal-500/10 border-teal-100 dark:border-teal-500/20">
        <GoDaddyIcon className="w-6 h-6 text-teal-600 dark:text-teal-400" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-black text-slate-900 dark:text-white tracking-tight">{account.label || 'GoDaddy'}</span>
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
            <span className="text-[10px] font-black text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Active</span>
          </span>
          <span className="px-2 py-0.5 rounded-lg bg-teal-50 dark:bg-teal-500/10 border border-teal-100 dark:border-teal-500/20 text-[10px] font-bold text-teal-600 dark:text-teal-400 uppercase">{account.record_type}</span>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm text-slate-500 dark:text-white/40">
            <span className="font-bold text-slate-700 dark:text-white/70">{account.domain}</span>
          </span>
          {account.target && (
            <span className="text-xs text-slate-400 dark:text-white/25">→ {account.target}</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={() => onEdit(account)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-slate-200 dark:border-white/10 text-slate-500 dark:text-white/40 text-xs font-bold bg-slate-50 dark:bg-white/[0.04] hover:bg-slate-100 dark:hover:bg-white/[0.08] transition-all"
        >
          <Edit3 className="w-3.5 h-3.5" /> Edit
        </button>
        <button
          onClick={() => onDelete(account.id)}
          disabled={deleting}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-red-200 dark:border-red-500/20 text-red-500 dark:text-red-400 text-xs font-bold bg-red-50 dark:bg-red-500/[0.07] hover:bg-red-100 dark:hover:bg-red-500/15 transition-all disabled:opacity-50"
        >
          {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   GODADDY MODAL (Add / Edit)
════════════════════════════════════════════════════════ */
function GoDaddyModal({ isOpen, onClose, onSaved, editAccount, onToast }) {
  const [label, setLabel] = useState('');
  const [domain, setDomain] = useState('');
  const [recordType, setRecordType] = useState('A');
  const [target, setTarget] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [showApiSecret, setShowApiSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const isEdit = !!editAccount;

  useEffect(() => {
    if (isOpen) {
      setError('');
      setApiKey('');
      setApiSecret('');
      setShowApiKey(false);
      setShowApiSecret(false);
      if (editAccount) {
        setLabel(editAccount.label || '');
        setDomain(editAccount.domain || '');
        setRecordType(editAccount.record_type || 'A');
        setTarget(editAccount.target || '');
      } else {
        setLabel('');
        setDomain('');
        setRecordType('A');
        setTarget('');
      }
    }
  }, [isOpen, editAccount]);

  const handleSave = async () => {
    setSaving(true);
    setError('');
    try {
      const body = { label, domain, record_type: recordType, target };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      if (apiSecret.trim()) body.api_secret = apiSecret.trim();

      if (isEdit) {
        body.id = editAccount.id;
        const res = await fetch('/api/godaddy-accounts', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Update failed');
        onToast('GoDaddy account updated!');
      } else {
        if (!apiKey.trim() || !apiSecret.trim()) throw new Error('API Key and Secret are required for new accounts');
        const res = await fetch('/api/godaddy-accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Create failed');
        onToast('GoDaddy account added!');
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const inputCls = 'w-full px-4 py-3.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-2xl text-sm text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 focus:border-teal-400 dark:focus:border-teal-500/50 focus:ring-4 focus:ring-teal-500/5 outline-none transition-all';

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-black/30 dark:bg-black/60 backdrop-blur-sm"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 16 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            className={cn(
              'relative w-full max-w-[500px] rounded-3xl overflow-hidden',
              'bg-white dark:bg-[#111620]',
              'border border-slate-200/80 dark:border-white/[0.07]',
              'shadow-2xl shadow-slate-300/30 dark:shadow-black/50'
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-5 border-b border-slate-100 dark:border-white/[0.06]">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center border bg-teal-50 dark:bg-teal-500/10 border-teal-100 dark:border-teal-500/20">
                  <GoDaddyIcon className="w-5 h-5 text-teal-600 dark:text-teal-400" />
                </div>
                <div>
                  <h3 className="text-[15px] font-extrabold text-slate-900 dark:text-white leading-tight">
                    {isEdit ? 'Edit GoDaddy Account' : 'Add GoDaddy Account'}
                  </h3>
                  <p className="text-xs text-slate-400 dark:text-white/35 mt-0.5">DNS management for your domains</p>
                </div>
              </div>
              <button onClick={onClose} className="w-8 h-8 flex items-center justify-center rounded-full text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10 transition-all">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Form */}
            <div className="px-6 py-6 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Label</label>
                  <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Production" className={inputCls} />
                </div>
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Domain</label>
                  <input type="text" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="javoxir.online" className={inputCls} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Record Type</label>
                  <div className="grid grid-cols-2 gap-2">
                    {['A', 'CNAME'].map(t => (
                      <button key={t} type="button" onClick={() => setRecordType(t)}
                        className={cn(
                          'py-3 rounded-xl border text-sm font-bold transition-all',
                          recordType === t
                            ? 'border-teal-400 dark:border-teal-500 bg-teal-50 dark:bg-teal-500/10 text-teal-700 dark:text-teal-300'
                            : 'border-slate-200 dark:border-white/[0.08] text-slate-500 dark:text-white/40 hover:border-slate-300'
                        )}
                      >{t}</button>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Target</label>
                  <input type="text" value={target} onChange={(e) => setTarget(e.target.value)}
                    placeholder={recordType === 'A' ? '123.45.67.89' : 'cname.vercel-dns.com'}
                    className={inputCls} />
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">API Key</label>
                  {isEdit && editAccount.has_api_key && !apiKey && (
                    <span className="flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                      <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                      <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Saved</span>
                    </span>
                  )}
                </div>
                <div className="relative">
                  <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                  <input type={showApiKey ? 'text' : 'password'} value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                    placeholder={isEdit && editAccount.has_api_key ? '••••••••  (saved — paste to replace)' : 'GoDaddy API Key'}
                    className={inputCls + ' pl-11 pr-12'} />
                  <button type="button" onClick={() => setShowApiKey(!showApiKey)} className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-300 dark:text-white/25 hover:text-slate-500 dark:hover:text-white/60 transition-colors">
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">API Secret</label>
                  {isEdit && editAccount.has_api_secret && !apiSecret && (
                    <span className="flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                      <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                      <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Saved</span>
                    </span>
                  )}
                </div>
                <div className="relative">
                  <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                  <input type={showApiSecret ? 'text' : 'password'} value={apiSecret} onChange={(e) => setApiSecret(e.target.value)}
                    placeholder={isEdit && editAccount.has_api_secret ? '••••••••  (saved — paste to replace)' : 'GoDaddy API Secret'}
                    className={inputCls + ' pl-11 pr-12'} />
                  <button type="button" onClick={() => setShowApiSecret(!showApiSecret)} className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-300 dark:text-white/25 hover:text-slate-500 dark:hover:text-white/60 transition-colors">
                    {showApiSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <AnimatePresence>
                {error && (
                  <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
                    className="flex items-center gap-3 p-4 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-2xl text-red-600 dark:text-red-400 text-sm font-medium">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    {error}
                  </motion.div>
                )}
              </AnimatePresence>

              <div className="pt-1">
                <button onClick={handleSave} disabled={!domain.trim() || saving}
                  className={cn(
                    'w-full flex items-center justify-center gap-2.5 py-4 rounded-2xl text-sm font-bold transition-all',
                    domain.trim() && !saving
                      ? 'bg-teal-600 text-white hover:bg-teal-700 shadow-lg shadow-teal-200/50 dark:shadow-teal-900/30'
                      : 'bg-slate-100 dark:bg-white/[0.05] text-slate-400 dark:text-white/25 cursor-not-allowed'
                  )}>
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                  {saving ? 'Validating & Saving...' : isEdit ? 'Update Account' : 'Add Account'}
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
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

  // GoDaddy multi-account state
  const [gdAccounts, setGdAccounts] = useState([]);
  const [gdLoading, setGdLoading] = useState(true);
  const [gdModalOpen, setGdModalOpen] = useState(false);
  const [gdEditAccount, setGdEditAccount] = useState(null);
  const [gdDeleting, setGdDeleting] = useState(null);

  const loadIntegrations = useCallback(async () => {
    const data = await getIntegrations();
    setIntegrations(data);
    setLoading(false);
  }, []);

  const loadGdAccounts = useCallback(async () => {
    setGdLoading(true);
    try {
      const res = await fetch('/api/godaddy-accounts');
      if (res.ok) {
        const data = await res.json();
        setGdAccounts(data.accounts || []);
      }
    } catch (e) {
      console.warn('Failed to load GoDaddy accounts:', e);
    } finally {
      setGdLoading(false);
    }
  }, []);

  const handleGdDelete = useCallback(async (id) => {
    setGdDeleting(id);
    try {
      const res = await fetch('/api/godaddy-accounts', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      if (res.ok) {
        setToast({ message: 'GoDaddy account removed.', type: 'success' });
        loadGdAccounts();
      }
    } catch (e) {
      console.warn('Delete failed:', e);
    } finally {
      setGdDeleting(null);
    }
  }, [loadGdAccounts]);

  useEffect(() => { loadIntegrations(); loadGdAccounts(); }, [loadIntegrations, loadGdAccounts]);

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

  const connectedCount = [integrations.github, integrations.gitlab].filter(i => i?.connected).length + (gdAccounts.length > 0 ? 1 : 0);
  const totalServices = 3; // GitHub, GitLab, GoDaddy

  if (loading) {
    return (
      <div className="min-h-full bg-[#fefcfa] dark:bg-[#0d1117] flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-full border-[3px] border-slate-200 dark:border-indigo-500/20 border-t-indigo-500 animate-spin" />
          <p className="text-xs font-bold text-slate-400 dark:text-white/20 uppercase tracking-[0.25em]">Loading</p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full bg-[#fefcfa] dark:bg-[#0d1117] overflow-y-auto">
      {toast && <Toast message={toast.message} type={toast.type} onDone={() => setToast(null)} />}

      <IntegrationModal
        isOpen={modalState.isOpen}
        onClose={() => setModalState({ isOpen: false, type: null })}
        type={modalState.type}
        integration={integrations[modalState.type]}
        onRefresh={loadIntegrations}
        onToast={(msg) => setToast({ message: msg, type: 'success' })}
      />

      <GoDaddyModal
        isOpen={gdModalOpen}
        onClose={() => { setGdModalOpen(false); setGdEditAccount(null); }}
        onSaved={loadGdAccounts}
        editAccount={gdEditAccount}
        onToast={(msg) => setToast({ message: msg, type: 'success' })}
      />

      <div className="px-8 lg:px-10 py-10">

        {/* ══ Page Header ══ */}
        <div className="mb-8">
          <h1 className="text-[32px] font-extrabold text-slate-900 dark:text-white tracking-tight">
            Integrations
          </h1>
          <p className="text-[15px] text-slate-500 dark:text-slate-400 mt-2 max-w-lg">
            Discover pre-built integrations that let you connect to APIs, services, and tools to extend your app&apos;s capabilities.
          </p>
        </div>

        {/* ══ Search ══ */}
        <div className="mb-8">
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              placeholder="Search integrations..."
              className="w-full pl-10 pr-4 py-2.5 text-[13px] bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl text-slate-700 dark:text-slate-300 placeholder:text-slate-400 outline-none focus:border-indigo-400 transition"
            />
          </div>
        </div>

        {/* ══ Connectors Section ══ */}
        <div className="mb-10">
          <h2 className="text-[20px] font-bold text-slate-900 dark:text-white mb-1">Connectors</h2>
          <p className="text-[14px] text-slate-500 dark:text-slate-400 mb-6">Quick OAuth connections to popular services, supported by Lucid AI.</p>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {/* GitHub Card */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all">
              <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-white/[0.06] border border-slate-200/60 dark:border-[#2d333b] flex items-center justify-center mb-4">
                <Github className="w-6 h-6 text-slate-800 dark:text-white" />
              </div>
              <h3 className="text-[16px] font-bold text-slate-900 dark:text-white mb-1">GitHub</h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4 flex-1">
                {integrations.github?.connected
                  ? <>Connected as <span className="font-semibold text-slate-700 dark:text-slate-300">{integrations.github.username}</span></>
                  : 'Push code, create repos, and manage your projects on GitHub.'}
              </p>
              <button
                onClick={() => setModalState({ isOpen: true, type: 'github' })}
                className={cn(
                  "w-full py-3 rounded-xl text-[13px] font-semibold transition-all border",
                  integrations.github?.connected
                    ? "bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]"
                    : "bg-slate-900 dark:bg-white border-slate-900 dark:border-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100"
                )}
              >
                {integrations.github?.connected ? 'Manage' : 'How to use'}
              </button>
            </div>

            {/* GitLab Card */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all">
              <div className="w-12 h-12 rounded-xl bg-orange-50 dark:bg-orange-500/10 border border-orange-200/60 dark:border-orange-500/20 flex items-center justify-center mb-4">
                <GitLabIcon className="w-6 h-6 text-orange-500" />
              </div>
              <h3 className="text-[16px] font-bold text-slate-900 dark:text-white mb-1">GitLab</h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4 flex-1">
                {integrations.gitlab?.connected
                  ? <>Connected as <span className="font-semibold text-slate-700 dark:text-slate-300">{integrations.gitlab.username}</span></>
                  : 'Push code to self-hosted or cloud GitLab instances.'}
              </p>
              <button
                onClick={() => setModalState({ isOpen: true, type: 'gitlab' })}
                className={cn(
                  "w-full py-3 rounded-xl text-[13px] font-semibold transition-all border",
                  integrations.gitlab?.connected
                    ? "bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]"
                    : "bg-slate-900 dark:bg-white border-slate-900 dark:border-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100"
                )}
              >
                {integrations.gitlab?.connected ? 'Manage' : 'How to use'}
              </button>
            </div>

            {/* GoDaddy Card */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all">
              <div className="w-12 h-12 rounded-xl bg-teal-50 dark:bg-teal-500/10 border border-teal-200/60 dark:border-teal-500/20 flex items-center justify-center mb-4">
                <GoDaddyIcon className="w-6 h-6 text-teal-600 dark:text-teal-400" />
              </div>
              <h3 className="text-[16px] font-bold text-slate-900 dark:text-white mb-1">GoDaddy DNS</h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4 flex-1">
                {gdAccounts.length > 0
                  ? <>{gdAccounts.length} account{gdAccounts.length > 1 ? 's' : ''} connected</>
                  : 'Manage your domain DNS records and deploy to custom domains.'}
              </p>
              <button
                onClick={() => { setGdEditAccount(null); setGdModalOpen(true); }}
                className={cn(
                  "w-full py-3 rounded-xl text-[13px] font-semibold transition-all border",
                  gdAccounts.length > 0
                    ? "bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]"
                    : "bg-slate-900 dark:bg-white border-slate-900 dark:border-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100"
                )}
              >
                {gdAccounts.length > 0 ? 'How to use' : 'How to use'}
              </button>
            </div>

            {/* Coming Soon — Stripe */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col opacity-60">
              <div className="w-12 h-12 rounded-xl bg-purple-50 dark:bg-purple-500/10 border border-purple-200/60 dark:border-purple-500/20 flex items-center justify-center mb-4">
                <span className="text-[20px] font-black text-purple-600 dark:text-purple-400">S</span>
              </div>
              <h3 className="text-[16px] font-bold text-slate-900 dark:text-white mb-1">Stripe</h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4 flex-1">
                Sell products or subscriptions and get paid online.
              </p>
              <button disabled className="w-full py-3 rounded-xl text-[13px] font-semibold border bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-400 dark:text-slate-600 cursor-not-allowed">
                Coming Soon
              </button>
            </div>

            {/* Coming Soon — Slack */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col opacity-60">
              <div className="w-12 h-12 rounded-xl bg-yellow-50 dark:bg-yellow-500/10 border border-yellow-200/60 dark:border-yellow-500/20 flex items-center justify-center mb-4">
                <span className="text-[18px]">💬</span>
              </div>
              <h3 className="text-[16px] font-bold text-slate-900 dark:text-white mb-1">Slack</h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4 flex-1">
                Send messages and manage Slack as a user.
              </p>
              <button disabled className="w-full py-3 rounded-xl text-[13px] font-semibold border bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-400 dark:text-slate-600 cursor-not-allowed">
                Coming Soon
              </button>
            </div>

            {/* Coming Soon — Notion */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col opacity-60">
              <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-white/[0.06] border border-slate-200/60 dark:border-[#2d333b] flex items-center justify-center mb-4">
                <span className="text-[20px] font-black text-slate-800 dark:text-white">N</span>
              </div>
              <h3 className="text-[16px] font-bold text-slate-900 dark:text-white mb-1">Notion</h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4 flex-1">
                Organize and sync knowledge or project data.
              </p>
              <button disabled className="w-full py-3 rounded-xl text-[13px] font-semibold border bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-400 dark:text-slate-600 cursor-not-allowed">
                Coming Soon
              </button>
            </div>
          </div>
        </div>

        {/* ══ GoDaddy Domain Accounts (if any) ══ */}
        {gdAccounts.length > 0 && (
          <div className="mb-10">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-[20px] font-bold text-slate-900 dark:text-white mb-1">Domain Accounts</h2>
                <p className="text-[14px] text-slate-500 dark:text-slate-400">{gdAccounts.length} GoDaddy account{gdAccounts.length > 1 ? 's' : ''} configured.</p>
              </div>
              <button
                onClick={() => { setGdEditAccount(null); setGdModalOpen(true); }}
                className="flex items-center gap-2 px-4 py-2 text-[13px] font-semibold text-white bg-slate-900 dark:bg-white dark:text-slate-900 rounded-full hover:bg-slate-800 dark:hover:bg-slate-100 shadow-sm transition-all"
              >
                <Plus className="w-4 h-4" /> Add Account
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {gdAccounts.map(acc => (
                <div key={acc.id} className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6 flex flex-col hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all">
                  <div className="flex items-start justify-between mb-3">
                    <div className="w-10 h-10 rounded-xl bg-teal-50 dark:bg-teal-500/10 border border-teal-200/60 dark:border-teal-500/20 flex items-center justify-center">
                      <Globe className="w-5 h-5 text-teal-600 dark:text-teal-400" />
                    </div>
                    <span className="px-2 py-0.5 rounded-md bg-teal-50 dark:bg-teal-500/10 border border-teal-200 dark:border-teal-500/20 text-[10px] font-bold text-teal-600 dark:text-teal-400 uppercase">{acc.record_type}</span>
                  </div>
                  <h3 className="text-[15px] font-bold text-slate-900 dark:text-white mb-1">{acc.label || 'Default'}</h3>
                  <p className="text-[13px] text-slate-500 dark:text-slate-400 mb-1">
                    <span className="font-semibold text-slate-700 dark:text-slate-300">{acc.domain}</span>
                  </p>
                  {acc.target && (
                    <p className="text-[12px] text-slate-400 dark:text-slate-500 mb-4">→ {acc.target}</p>
                  )}
                  <div className="flex items-center gap-2 mt-auto pt-3">
                    <button
                      onClick={() => { setGdEditAccount(acc); setGdModalOpen(true); }}
                      className="flex-1 py-2.5 rounded-xl text-[12px] font-semibold border bg-white dark:bg-[#0d1117] border-slate-200 dark:border-[#2d333b] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-all"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleGdDelete(acc.id)}
                      disabled={gdDeleting === acc.id}
                      className="py-2.5 px-4 rounded-xl text-[12px] font-semibold border border-red-200 dark:border-red-500/20 text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all disabled:opacity-50"
                    >
                      {gdDeleting === acc.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Delete'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ══ Git Identity ══ */}
        <div className="mb-10">
          <h2 className="text-[20px] font-bold text-slate-900 dark:text-white mb-1">Git Identity</h2>
          <p className="text-[14px] text-slate-500 dark:text-slate-400 mb-6">The author details attached to every commit made by Lucid AI on your behalf.</p>

          <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] p-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
              <div className="space-y-1.5">
                <label className="text-[12px] font-semibold text-slate-500 dark:text-slate-400">Author Name</label>
                <div className="relative group">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/20 group-focus-within:text-indigo-500 transition-colors" />
                  <input
                    value={gitUsername}
                    onChange={(e) => setGitUsername(e.target.value)}
                    placeholder="John Doe"
                    className="w-full pl-9 pr-4 py-2.5 rounded-xl text-sm outline-none transition-all bg-white dark:bg-[#0d1117] border border-slate-200 dark:border-[#2d333b] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/15 hover:border-slate-300 dark:hover:border-[#444c56] focus:border-indigo-400 dark:focus:border-indigo-500/50"
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-[12px] font-semibold text-slate-500 dark:text-slate-400">Author Email</label>
                <div className="relative group">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/20 group-focus-within:text-indigo-500 transition-colors" />
                  <input
                    value={gitEmail}
                    onChange={(e) => setGitEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="w-full pl-9 pr-4 py-2.5 rounded-xl text-sm outline-none transition-all bg-white dark:bg-[#0d1117] border border-slate-200 dark:border-[#2d333b] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/15 hover:border-slate-300 dark:hover:border-[#444c56] focus:border-indigo-400 dark:focus:border-indigo-500/50"
                  />
                </div>
              </div>
            </div>

            <div className="flex items-center justify-between">
              <p className="text-[12px] italic text-slate-400 dark:text-white/20">Changes apply on the next commit</p>
              <button
                onClick={saveGitSettings}
                disabled={savingGit}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 shadow-sm transition-all active:scale-[0.98] disabled:opacity-60"
              >
                {savingGit ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                Save Identity
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

