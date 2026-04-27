'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';
import {
  X, Github, Loader2, Check, Globe, Lock, Eye, EyeOff,
  ExternalLink, ArrowRight, AlertCircle, Copy, Info,
  Settings2, FileCode2, Search, ChevronDown
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  getIntegrations,
  saveGitHubIntegration,
  saveGitLabIntegration,
  saveBitbucketIntegration,
} from '@/lib/integrations';

/* ─── Provider SVG Icons ─── */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}
function BitbucketIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M.778 1.213a.768.768 0 00-.768.892l3.263 19.81c.084.5.515.868 1.022.873H19.95a.772.772 0 00.77-.646L24.003 2.104a.768.768 0 00-.768-.892zM14.52 15.53H9.522L8.17 8.466h7.561z" />
    </svg>
  );
}

const PROVIDERS = [
  {
    id: 'github',
    name: 'GitHub',
    Icon: Github,
    color: 'slate',
    tokenDoc: 'https://github.com/settings/tokens/new?scopes=repo',
    scope: 'repo',
    placeholder: 'ghp_••••••••••••••••••',
  },
  {
    id: 'gitlab',
    name: 'GitLab',
    Icon: GitLabIcon,
    color: 'orange',
    tokenDoc: 'https://gitlab.com/-/profile/personal_access_tokens',
    scope: 'api',
    placeholder: 'glpat-••••••••••••••••',
  },
  {
    id: 'bitbucket',
    name: 'Bitbucket',
    Icon: BitbucketIcon,
    color: 'blue',
    tokenDoc: 'https://bitbucket.org/account/settings/app-passwords/',
    scope: 'repository:write',
    placeholder: 'App password',
  },
];

/* ═══════════════════════════════════════════════════════
   STEPS:
   1. pick_provider  — choose GitHub / GitLab / Bitbucket
   2. connect        — if not connected, enter token
   3. repo_config    — name + visibility
   4. exporting      — server-side export running
   5. success        — done with link
   ═══════════════════════════════════════════════════════ */

/**
 * Reusable Export Code Modal.
 *
 * @param {object} props
 * @param {boolean} props.isOpen
 * @param {() => void} props.onClose
 * @param {string} props.projectSlug — pre-fills repo name
 * @param {string} props.projectId — platform project ID
 */
export default function ExportCodeModal({
  isOpen,
  onClose,
  projectSlug = '',
  projectId = '',
  // Set true when launched via the upgrade modal's "Skip for testing" path —
  // the export still runs end-to-end on the user's plan, the server just
  // skips the canExportCode gate. Logs a warning server-side.
  bypassLimits = false,
}) {
  const [step, setStep] = useState('pick_provider');
  const [provider, setProvider] = useState(null);
  const [integrations, setIntegrations] = useState({});
  const [loading, setLoading] = useState(true);

  // Connect step
  const [token, setToken] = useState('');
  const [bbUsername, setBbUsername] = useState('');
  const [gitlabHost, setGitlabHost] = useState('https://gitlab.com');
  const [showToken, setShowToken] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState('');

  // Repo config step
  const [repoName, setRepoName] = useState(projectSlug);
  const [isPrivate, setIsPrivate] = useState(true);
  const [includeCICD, setIncludeCICD] = useState(false);
  const [gitlabInviteUser, setGitlabInviteUser] = useState('');

  // GoDaddy account selection
  const [gdAccounts, setGdAccounts] = useState([]);
  const [selectedGdAccount, setSelectedGdAccount] = useState('');
  const [gdDropdownOpen, setGdDropdownOpen] = useState(false);

  // User autocomplete
  const [userSuggestions, setUserSuggestions] = useState([]);
  const [searchingUsers, setSearchingUsers] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inviteInputRef = useRef(null);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });

  // Export step
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [exportResult, setExportResult] = useState(null);

  // Load integrations + GoDaddy accounts on open
  useEffect(() => {
    if (isOpen) {
      setStep('pick_provider');
      setProvider(null);
      setToken('');
      setBbUsername('');
      setConnectError('');
      setExportError('');
      setExportResult(null);
      setRepoName(projectSlug);
      setIsPrivate(true);
      setGitlabInviteUser('');
      setSelectedGdAccount('');
      setUserSuggestions([]);
      loadIntegrations();
      loadGdAccounts();
    }
  }, [isOpen, projectSlug]);

  const loadIntegrations = async () => {
    setLoading(true);
    const data = await getIntegrations();
    setIntegrations(data);
    setLoading(false);
  };

  const loadGdAccounts = async () => {
    try {
      const res = await fetch('/api/godaddy-accounts');
      if (res.ok) {
        const data = await res.json();
        setGdAccounts(data.accounts || []);
      }
    } catch (e) {
      console.warn('Failed to load GoDaddy accounts:', e);
    }
  };

  // User autocomplete — debounced search
  const searchTimeoutRef = useRef(null);
  const handleInviteUserChange = (val) => {
    setGitlabInviteUser(val);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (val.trim().length < 2) {
      setUserSuggestions([]);
      setShowSuggestions(false);
      return;
    }
    searchTimeoutRef.current = setTimeout(async () => {
      setSearchingUsers(true);
      try {
        const integration = integrations[provider];
        if (!integration?.token) return;
        let users = [];
        if (provider === 'github') {
          const res = await fetch(`https://api.github.com/search/users?q=${encodeURIComponent(val)}&per_page=5`, {
            headers: { 'Authorization': `Bearer ${integration.token}`, 'Accept': 'application/vnd.github+json' },
          });
          if (res.ok) {
            const data = await res.json();
            users = (data.items || []).map(u => ({ username: u.login, avatar: u.avatar_url }));
          }
        } else if (provider === 'gitlab') {
          const host = (integration.host || 'https://gitlab.com').replace(/\/+$/, '');
          const res = await fetch(`${host}/api/v4/users?search=${encodeURIComponent(val)}&per_page=5`, {
            headers: { 'PRIVATE-TOKEN': integration.token },
          });
          if (res.ok) {
            const data = await res.json();
            users = (data || []).map(u => ({ username: u.username, avatar: u.avatar_url }));
          }
        }
        setUserSuggestions(users);
        if (users.length > 0) {
          const rect = inviteInputRef.current?.getBoundingClientRect();
          if (rect) setDropdownPos({ top: rect.bottom + 4, left: rect.left, width: rect.width });
        }
        setShowSuggestions(users.length > 0);
      } catch (e) {
        console.warn('User search failed:', e);
      } finally {
        setSearchingUsers(false);
      }
    }, 400);
  };

  const handleProviderSelect = (providerId) => {
    setProvider(providerId);
    const integration = integrations[providerId];
    if (integration?.connected) {
      // Already connected — skip to repo naming
      setStep('repo_config');
    } else {
      setStep('connect');
    }
  };

  const handleConnect = async () => {
    setConnecting(true);
    setConnectError('');
    let result;

    if (provider === 'github') {
      result = await saveGitHubIntegration(token.trim());
    } else if (provider === 'gitlab') {
      result = await saveGitLabIntegration(token.trim(), gitlabHost.trim());
    } else if (provider === 'bitbucket') {
      result = await saveBitbucketIntegration(bbUsername.trim(), token.trim());
    }

    if (result?.ok) {
      await loadIntegrations();
      setStep('repo_config');
      setToken('');
    } else {
      setConnectError(result?.error || 'Connection failed');
    }
    setConnecting(false);
  };

  const handleExport = useCallback(async () => {
    setExporting(true);
    setExportError('');
    setStep('exporting');

    try {
      const res = await fetch('/api/export-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          projectId,
          provider,
          repoName: repoName.trim().toLowerCase().replace(/[^a-z0-9-_.]/g, '-'),
          isPrivate,
          includeCICD,
          gitlabInviteUser: gitlabInviteUser.trim(),
          godaddyAccountId: selectedGdAccount || undefined,
          bypassLimits,
        }),
      });

      const data = await res.json();

      // Token expired — send user to reconnect flow
      if (data.needsReconnect) {
        setExportError(data.error || 'Token expired. Please reconnect.');
        setToken(''); // Clear old token
        setStep('connect'); // Go to token entry step
        setExporting(false);
        return;
      }

      // Hard failure — repo wasn't even created
      if (!res.ok && !data.ok) {
        throw new Error(data.error || 'Export failed');
      }

      // Partial or full success — repo was created
      setExportResult(data);
      setStep('success');
    } catch (err) {
      setExportError(err.message);
      setStep('repo_config');
    } finally {
      setExporting(false);
    }
  }, [projectId, provider, repoName, isPrivate, includeCICD, gitlabInviteUser, selectedGdAccount]);

  const providerData = PROVIDERS.find(p => p.id === provider);
  const integration = integrations[provider];

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-black/30 dark:bg-black/60 backdrop-blur-sm"
          />

          {/* Modal */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 16 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            className={cn(
              'relative w-full max-w-[480px] rounded-2xl flex flex-col',
              'bg-white dark:bg-[#111620]',
              'border border-slate-200/80 dark:border-white/[0.07]',
              'shadow-2xl shadow-slate-300/30 dark:shadow-black/50',
              'max-h-[85vh]'
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-white/[0.06]">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-sm">
                  <ExternalLink className="w-4 h-4 text-white" />
                </div>
                <div>
                  <h3 className="text-[14px] font-bold text-slate-900 dark:text-white leading-tight">
                    Export Code
                  </h3>
                  <p className="text-[11px] text-slate-400 dark:text-white/35">
                    {step === 'pick_provider' && 'Choose a destination'}
                    {step === 'connect' && `Connect ${providerData?.name}`}
                    {step === 'repo_config' && 'Configure repository'}
                    {step === 'exporting' && 'Exporting...'}
                    {step === 'success' && 'Export complete!'}
                  </p>
                </div>
              </div>
              <button
                onClick={onClose}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10 transition-all"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* ── Step 1: Pick Provider ── */}
            {step === 'pick_provider' && (
              <div className="px-5 py-5 space-y-2.5">
                {loading ? (
                  <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
                  </div>
                ) : (
                  PROVIDERS.map((p) => {
                    const connected = integrations[p.id]?.connected;
                    return (
                      <button
                        key={p.id}
                        onClick={() => handleProviderSelect(p.id)}
                        className={cn(
                          'w-full flex items-center gap-3 px-4 py-3 rounded-xl border transition-all group text-left',
                          'hover:border-indigo-300 dark:hover:border-indigo-500/40 hover:shadow-lg hover:shadow-indigo-500/5',
                          connected
                            ? 'border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-500/5'
                            : 'border-slate-200 dark:border-white/[0.07] bg-white dark:bg-white/[0.02]'
                        )}
                      >
                        <div className={cn(
                          'w-9 h-9 rounded-lg flex items-center justify-center border shrink-0',
                          p.id === 'github' && 'bg-slate-50 dark:bg-white/5 border-slate-200 dark:border-white/10',
                          p.id === 'gitlab' && 'bg-orange-50 dark:bg-orange-500/10 border-orange-100 dark:border-orange-500/20',
                          p.id === 'bitbucket' && 'bg-blue-50 dark:bg-blue-500/10 border-blue-100 dark:border-blue-500/20',
                        )}>
                          <p.Icon className={cn(
                            'w-5 h-5',
                            p.id === 'github' && 'text-slate-800 dark:text-white',
                            p.id === 'gitlab' && 'text-orange-500',
                            p.id === 'bitbucket' && 'text-blue-600',
                          )} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-bold text-slate-900 dark:text-white">{p.name}</span>
                            {connected && (
                              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                                <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Connected</span>
                              </span>
                            )}
                          </div>
                          <p className="text-[11px] text-slate-400 dark:text-white/30 mt-0.5">
                            {connected
                              ? `Signed in as ${integrations[p.id]?.username || integrations[p.id]?.displayName}`
                              : `Requires ${p.scope} scope`}
                          </p>
                        </div>
                        <ArrowRight className="w-4 h-4 text-slate-300 dark:text-white/20 group-hover:text-indigo-500 transition-colors" />
                      </button>
                    );
                  })
                )}
              </div>
            )}

            {/* ── Step 2: Connect Provider ── */}
            {step === 'connect' && providerData && (
              <div className="px-5 py-5 space-y-3.5">
                {provider === 'gitlab' && (
                  <div className="space-y-2">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Instance URL</label>
                    <div className="relative">
                      <Globe className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                      <input
                        type="url"
                        value={gitlabHost}
                        onChange={(e) => setGitlabHost(e.target.value)}
                        placeholder="https://gitlab.com"
                        className="w-full pl-11 pr-4 py-2.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-xl text-[13px] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/5 outline-none transition-all"
                      />
                    </div>
                  </div>
                )}

                {provider === 'bitbucket' && (
                  <div className="space-y-2">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Bitbucket Username</label>
                    <input
                      type="text"
                      value={bbUsername}
                      onChange={(e) => setBbUsername(e.target.value)}
                      placeholder="your-username"
                      className="w-full px-3.5 py-2.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-xl text-[13px] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/5 outline-none transition-all"
                    />
                  </div>
                )}

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">
                      {provider === 'bitbucket' ? 'App Password' : 'Personal Access Token'}
                    </label>
                    <a
                      href={providerData.tokenDoc}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] font-bold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 flex items-center gap-1"
                    >
                      Generate <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                    <input
                      type={showToken ? 'text' : 'password'}
                      value={token}
                      onChange={(e) => { setToken(e.target.value); setConnectError(''); }}
                      placeholder={providerData.placeholder}
                      className="w-full pl-11 pr-12 py-2.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-xl text-[13px] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 font-mono focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/5 outline-none transition-all"
                    />
                    <button
                      type="button"
                      onClick={() => setShowToken(!showToken)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-300 dark:text-white/25 hover:text-slate-500 dark:hover:text-white/60 transition-colors"
                    >
                      {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-400 dark:text-white/30">
                    Required scope: <code className="text-indigo-600 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-500/[0.15] px-1.5 py-0.5 rounded-md font-mono">{providerData.scope}</code>
                  </p>
                </div>

                {connectError && (
                  <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-2.5 p-3 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-xl text-red-600 dark:text-red-400 text-[12px] font-medium">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    {connectError}
                  </motion.div>
                )}

                <div className="flex gap-3 pt-1">
                  <button
                    onClick={() => { setStep('pick_provider'); setToken(''); setConnectError(''); }}
                    className="px-4 py-2.5 rounded-xl text-[13px] font-medium text-slate-500 hover:text-slate-700 dark:text-white/40 dark:hover:text-white transition-colors"
                  >
                    Back
                  </button>
                  <button
                    onClick={handleConnect}
                    disabled={!token.trim() || connecting || (provider === 'bitbucket' && !bbUsername.trim())}
                    className={cn(
                      'flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-[13px] font-bold transition-all',
                      token.trim() && !connecting
                        ? 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm shadow-indigo-200/50 dark:shadow-indigo-900/30'
                        : 'bg-slate-100 dark:bg-white/[0.05] text-slate-400 dark:text-white/25 cursor-not-allowed'
                    )}
                  >
                    {connecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                    {connecting ? 'Connecting...' : `Connect ${providerData.name}`}
                  </button>
                </div>
              </div>
            )}

            {step === 'repo_config' && providerData && (
              <div className="px-5 py-5 space-y-4 overflow-y-auto overflow-x-visible max-h-[calc(85vh-60px)] pb-4">

                {/* Repo name */}
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Repository Name</label>
                  <input
                    type="text"
                    value={repoName}
                    onChange={(e) => setRepoName(e.target.value)}
                    placeholder="my-awesome-project"
                    className="w-full px-3.5 py-2.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-xl text-[13px] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/5 outline-none transition-all"
                  />
                </div>

                {/* Visibility */}
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Visibility</label>
                  <div className="grid grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={() => setIsPrivate(true)}
                      className={cn(
                        'flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border text-[13px] font-medium transition-all text-left',
                        isPrivate
                          ? 'border-indigo-400 dark:border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-300'
                          : 'border-slate-200 dark:border-white/[0.08] text-slate-600 dark:text-white/40 hover:border-slate-300'
                      )}
                    >
                      <Lock className="w-4 h-4" />
                      Private
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsPrivate(false)}
                      className={cn(
                        'flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border text-[13px] font-medium transition-all text-left',
                        !isPrivate
                          ? 'border-indigo-400 dark:border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-300'
                          : 'border-slate-200 dark:border-white/[0.08] text-slate-600 dark:text-white/40 hover:border-slate-300'
                      )}
                    >
                      <Globe className="w-4 h-4" />
                      Public
                    </button>
                  </div>
                </div>

                {/* CI/CD Pipeline Toggle */}
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">CI/CD Pipeline</label>
                  <button
                    type="button"
                    onClick={() => setIncludeCICD(!includeCICD)}
                    className={cn(
                      'w-full flex items-center gap-3 px-3.5 py-3 rounded-xl border text-left transition-all',
                      includeCICD
                        ? 'border-emerald-400 dark:border-emerald-500/40 bg-emerald-50 dark:bg-emerald-500/10'
                        : 'border-slate-200 dark:border-white/[0.08] hover:border-slate-300 dark:hover:border-white/[0.12]'
                    )}
                  >
                    <div className={cn(
                      'w-9 h-9 rounded-lg flex items-center justify-center border shrink-0',
                      includeCICD
                        ? 'bg-emerald-100 dark:bg-emerald-500/20 border-emerald-200 dark:border-emerald-500/30'
                        : 'bg-slate-50 dark:bg-white/[0.04] border-slate-200 dark:border-white/[0.08]'
                    )}>
                      <Settings2 className={cn(
                        'w-4 h-4',
                        includeCICD ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-400 dark:text-white/30'
                      )} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={cn(
                          'text-[13px] font-bold',
                          includeCICD ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-600 dark:text-white/50'
                        )}>Include CI/CD Pipeline</span>
                        {includeCICD && (
                          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30">
                            <Check className="w-3 h-3 text-emerald-500" />
                          </span>
                        )}
                      </div>
                      <p className="text-[10px] text-slate-400 dark:text-white/30 mt-0.5">
                        Adds Dockerfile, nginx.conf, .gitlab-ci.yml, Makefile
                      </p>
                    </div>
                    <div className={cn(
                      'w-9 h-[18px] rounded-full transition-all relative',
                      includeCICD ? 'bg-emerald-500' : 'bg-slate-200 dark:bg-white/10'
                    )}>
                      <div className={cn(
                        'w-3.5 h-3.5 rounded-full bg-white shadow-sm absolute top-[2px] transition-all',
                        includeCICD ? 'left-[19px]' : 'left-[2px]'
                      )} />
                    </div>
                  </button>
                </div>

                {includeCICD && (provider === 'gitlab' || provider === 'github') && (
                  <div className="space-y-2 relative">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">Invite Team Member</label>
                    <div className="relative" ref={inviteInputRef}>
                      <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-white/25" />
                      <input
                        type="text"
                        value={gitlabInviteUser}
                        onChange={(e) => handleInviteUserChange(e.target.value)}
                        onFocus={() => {
                          if (userSuggestions.length > 0) {
                            const rect = inviteInputRef.current?.getBoundingClientRect();
                            if (rect) setDropdownPos({ top: rect.bottom + 4, left: rect.left, width: rect.width });
                            setShowSuggestions(true);
                          }
                        }}
                        onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
                        placeholder={`Search ${provider === 'github' ? 'GitHub' : 'GitLab'} users... (Optional)`}
                        className="w-full pl-11 pr-10 py-2.5 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-xl text-[13px] text-slate-800 dark:text-white placeholder-slate-300 dark:placeholder-white/20 focus:border-indigo-400 dark:focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/5 outline-none transition-all"
                      />
                      {searchingUsers && <Loader2 className="absolute right-4 top-1/2 -translate-y-1/2 w-4 h-4 text-indigo-500 animate-spin" />}
                    </div>
                    {/* Autocomplete dropdown — rendered via portal */}
                    {showSuggestions && userSuggestions.length > 0 && typeof document !== 'undefined' && createPortal(
                      <div
                        style={{ position: 'fixed', top: dropdownPos.top, left: dropdownPos.left, width: dropdownPos.width }}
                        className="z-[9999] bg-white dark:bg-[#1a2030] border border-slate-200 dark:border-white/[0.1] rounded-xl shadow-2xl max-h-[220px] overflow-y-auto"
                      >
                        {userSuggestions.map(u => (
                          <button
                            key={u.username}
                            type="button"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => { setGitlabInviteUser(u.username); setShowSuggestions(false); }}
                            className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-white/[0.06] text-left transition-colors"
                          >
                            {u.avatar && <img src={u.avatar} alt="" className="w-6 h-6 rounded-full" />}
                            <span className="text-[13px] font-semibold text-slate-800 dark:text-white truncate">{u.username}</span>
                          </button>
                        ))}
                      </div>,
                      document.body
                    )}
                    <p className="text-[11px] text-slate-400 dark:text-white/30">
                      Invited as Owner/Admin to the new repository.
                    </p>
                  </div>
                )}

                {/* GoDaddy Account selector */}
                {includeCICD && gdAccounts.length > 0 && (
                  <div className="space-y-2">
                    <label className="text-[11px] font-bold text-slate-400 dark:text-white/40 uppercase tracking-widest">GoDaddy DNS Account</label>
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => setGdDropdownOpen(!gdDropdownOpen)}
                        className={cn(
                          'w-full flex items-center justify-between px-4 py-3.5 rounded-2xl border text-sm text-left transition-all',
                          selectedGdAccount
                            ? 'border-teal-300 dark:border-teal-500/40 bg-teal-50 dark:bg-teal-500/10 text-teal-700 dark:text-teal-300 font-bold'
                            : 'border-slate-200 dark:border-white/[0.08] bg-slate-50 dark:bg-white/[0.04] text-slate-400 dark:text-white/30'
                        )}
                      >
                        {selectedGdAccount
                          ? (() => { const acc = gdAccounts.find(a => a.id === selectedGdAccount); return acc ? `${acc.label} — ${acc.domain}` : 'Select account'; })()
                          : 'None (skip DNS)'}
                        <ChevronDown className={cn('w-4 h-4 transition-transform', gdDropdownOpen && 'rotate-180')} />
                      </button>
                      {gdDropdownOpen && (
                        <div className="absolute left-0 right-0 top-full mt-1 z-20 bg-white dark:bg-[#1a2030] border border-slate-200 dark:border-white/[0.1] rounded-2xl shadow-xl overflow-hidden">
                          <button
                            type="button"
                            onClick={() => { setSelectedGdAccount(''); setGdDropdownOpen(false); }}
                            className={cn(
                              'w-full px-4 py-3 text-left text-sm hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors',
                              !selectedGdAccount ? 'text-indigo-600 dark:text-indigo-400 font-bold' : 'text-slate-500 dark:text-white/40'
                            )}
                          >
                            None (skip DNS)
                          </button>
                          {gdAccounts.map(acc => (
                            <button
                              key={acc.id}
                              type="button"
                              onClick={() => { setSelectedGdAccount(acc.id); setGdDropdownOpen(false); }}
                              className={cn(
                                'w-full flex items-center justify-between px-4 py-3 text-left text-sm hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors',
                                selectedGdAccount === acc.id ? 'text-teal-600 dark:text-teal-400 font-bold' : 'text-slate-700 dark:text-white/60'
                              )}
                            >
                              <span>{acc.label} — <span className="font-bold">{acc.domain}</span></span>
                              <span className="text-[10px] px-2 py-0.5 rounded-lg bg-slate-100 dark:bg-white/[0.06] text-slate-400 dark:text-white/30 font-bold">{acc.record_type}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-400 dark:text-white/30">
                      Creates {'{repo_name}'}.{'{domain}'} DNS record automatically.
                    </p>
                  </div>
                )}

                {exportError && (
                  <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-3 p-4 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-2xl text-red-600 dark:text-red-400 text-sm font-medium">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    {exportError}
                  </motion.div>
                )}

                <div className="flex gap-3 pt-1">
                  <button
                    onClick={() => setStep('pick_provider')}
                    className="px-4 py-2.5 rounded-xl text-[13px] font-medium text-slate-500 hover:text-slate-700 dark:text-white/40 dark:hover:text-white transition-colors"
                  >
                    Back
                  </button>
                  <button
                    onClick={handleExport}
                    disabled={!repoName.trim() || exporting}
                    className={cn(
                      'flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-[13px] font-bold transition-all',
                      repoName.trim() && !exporting
                        ? 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-lg shadow-indigo-200/50 dark:shadow-indigo-900/30'
                        : 'bg-slate-100 dark:bg-white/[0.05] text-slate-400 dark:text-white/25 cursor-not-allowed'
                    )}
                  >
                    {exporting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ExternalLink className="w-4 h-4" />}
                    Export to {providerData.name}
                  </button>
                </div>
              </div>
            )}

            {/* ── Step 4: Exporting ── */}
            {step === 'exporting' && (
              <div className="px-6 py-12 flex flex-col items-center text-center">
                <div className="w-16 h-16 rounded-2xl bg-indigo-50 dark:bg-indigo-500/10 border border-indigo-100 dark:border-indigo-500/20 flex items-center justify-center mb-4">
                  <Loader2 className="w-7 h-7 text-indigo-600 dark:text-indigo-400 animate-spin" />
                </div>
                <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-2">Exporting to {providerData?.name}...</h4>
                <p className="text-sm text-slate-400 dark:text-white/40">Creating repo, pushing code. This may take a moment.</p>
              </div>
            )}

            {/* ── Step 5: Success ── */}
            {step === 'success' && exportResult && (
              <div className="px-6 py-8 space-y-5">
                <div className="flex flex-col items-center text-center">
                  <div className={cn(
                    'w-16 h-16 rounded-2xl flex items-center justify-center mb-4 border',
                    exportResult.warning
                      ? 'bg-amber-50 dark:bg-amber-500/10 border-amber-100 dark:border-amber-500/20'
                      : 'bg-emerald-50 dark:bg-emerald-500/10 border-emerald-100 dark:border-emerald-500/20'
                  )}>
                    {exportResult.warning
                      ? <AlertCircle className="w-7 h-7 text-amber-500 dark:text-amber-400" />
                      : <Check className="w-7 h-7 text-emerald-600 dark:text-emerald-400" />}
                  </div>
                  <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-2">
                    {exportResult.warning ? 'Repo Created' : 'Export Successful!'}
                  </h4>
                  <p className="text-sm text-slate-500 dark:text-white/40">
                    {exportResult.warning
                      ? 'Repository created, but some steps had issues:'
                      : `${exportResult.filesExported || 0} files exported${exportResult.cicdAdded ? ' + CI/CD pipeline' : ''}`}
                  </p>
                </div>

                {/* Warning banner (if partial success) */}
                {exportResult.warning && (
                  <div className="flex items-start gap-2.5 px-4 py-3 bg-amber-50 dark:bg-amber-500/10 border border-amber-100 dark:border-amber-500/20 rounded-2xl">
                    <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                    <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
                      {exportResult.warning}
                    </p>
                  </div>
                )}

                {/* Repo link */}
                <div className="flex items-center gap-3 px-4 py-3 bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-white/[0.08] rounded-2xl">
                  <a
                    href={exportResult.repoUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex-1 text-sm text-indigo-600 dark:text-indigo-400 font-medium truncate hover:underline"
                  >
                    {exportResult.repoUrl}
                  </a>
                  <button
                    onClick={() => navigator.clipboard.writeText(exportResult.repoUrl)}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10 transition-all"
                    title="Copy URL"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                </div>

                {/* Export summary */}
                {!exportResult.warning && (
                  <div className="flex items-start gap-2.5 px-4 py-3 bg-slate-50 dark:bg-white/[0.03] border border-slate-200 dark:border-white/[0.06] rounded-2xl">
                    <Info className="w-4 h-4 text-slate-400 dark:text-white/30 shrink-0 mt-0.5" />
                    <p className="text-xs text-slate-500 dark:text-white/40 leading-relaxed">
                      Changes you make here won&apos;t sync automatically — re-export anytime to push the latest code.
                    </p>
                  </div>
                )}

                <div className="flex gap-3 pt-1">
                  <button
                    onClick={onClose}
                    className="flex-1 py-3.5 rounded-2xl text-sm font-medium text-slate-500 dark:text-white/40 border border-slate-200 dark:border-white/[0.08] hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-all"
                  >
                    Close
                  </button>
                  <a
                    href={exportResult.repoUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex-1 flex items-center justify-center gap-2 py-3.5 rounded-2xl text-sm font-bold bg-indigo-600 text-white hover:bg-indigo-700 shadow-lg shadow-indigo-200/50 dark:shadow-indigo-900/30 transition-all"
                  >
                    Open Repo <ExternalLink className="w-4 h-4" />
                  </a>
                </div>
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
