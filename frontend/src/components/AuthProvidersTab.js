'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  Database, Check, Eye, EyeOff, ExternalLink, Copy, Loader2,
  AlertCircle, Shield, ChevronDown, Info, X
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ═══════════════════════════════════════════════════════════
//  Provider metadata (icons, guides, redirect URL templates)
// ═══════════════════════════════════════════════════════════

const PROVIDER_META = {
  google: {
    label: 'Google',
    color: 'bg-red-500',
    guideUrl: 'https://console.cloud.google.com/apis/credentials',
    guideLabel: 'Google Cloud Console',
    instructions: 'Create OAuth 2.0 credentials → Add the Redirect URL below as an authorized redirect URI.',
  },
  github: {
    label: 'GitHub',
    color: 'bg-gray-900 dark:bg-gray-700',
    guideUrl: 'https://github.com/settings/developers',
    guideLabel: 'GitHub Developer Settings',
    instructions: 'Register a new OAuth App → Set the Authorization callback URL to the Redirect URL below.',
  },
  facebook: {
    label: 'Facebook',
    color: 'bg-blue-600',
    guideUrl: 'https://developers.facebook.com/apps/',
    guideLabel: 'Facebook Developers',
    instructions: 'Create an app → Add Facebook Login → Set the Valid OAuth Redirect URI to the Redirect URL below.',
  },
};

// ═══════════════════════════════════════════════════════════
//  Provider Card — configurable OAuth credentials
// ═══════════════════════════════════════════════════════════

function ProviderCard({ provider, supabaseRef, supabaseProjectId, onSaved }) {
  const meta = PROVIDER_META[provider.provider] || {};
  const [expanded, setExpanded] = useState(false);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const [copied, setCopied] = useState(false);

  const redirectUrl = `https://${supabaseRef}.supabase.co/auth/v1/callback`;
  const isEnabled = provider.enabled;

  const handleCopy = () => {
    navigator.clipboard.writeText(redirectUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSave = async () => {
    if (!clientId.trim() || !clientSecret.trim()) return;

    setSaving(true);
    setError(null);
    setSuccess(false);

    try {
      const res = await fetch('/api/auth-providers', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          supabaseProjectId,
          provider: provider.provider,
          clientId: clientId.trim(),
          clientSecret: clientSecret.trim(),
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || `HTTP ${res.status}`);
      }

      setSuccess(true);
      setClientId('');
      setClientSecret('');
      setTimeout(() => setSuccess(false), 5000);
      if (onSaved) onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={cn(
      "border rounded-2xl transition-all overflow-hidden",
      isEnabled
        ? "border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/30 dark:bg-emerald-500/5"
        : "border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900"
    )}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-4 text-left group"
      >
        <div className="flex items-center gap-3">
          <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center text-white text-xs font-bold", meta.color || 'bg-slate-500')}>
            {(meta.label || provider.provider)[0].toUpperCase()}
          </div>
          <div>
            <span className="text-sm font-bold text-slate-800 dark:text-slate-200">
              {meta.label || provider.provider}
            </span>
            <span className="ml-3">
              {isEnabled ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Active</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full">
                  <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600" />
                  <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase">Not configured</span>
                </span>
              )}
            </span>
          </div>
        </div>
        <ChevronDown className={cn("w-4 h-4 text-slate-400 transition-transform", expanded && "rotate-180")} />
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="px-5 pb-5 space-y-4 border-t border-slate-100 dark:border-slate-800 pt-4 animate-slide-down">
          {/* Success banner */}
          {success && (
            <div className="flex items-center gap-2 px-4 py-3 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-xl">
              <Check className="w-4 h-4 text-emerald-600" />
              <p className="text-sm text-emerald-600 dark:text-emerald-400 font-medium">
                {meta.label} OAuth is now live on your project!
              </p>
            </div>
          )}

          {/* Error banner */}
          {error && (
            <div className="flex items-center gap-2 px-4 py-3 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-xl">
              <AlertCircle className="w-4 h-4 text-red-500" />
              <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
              <button onClick={() => setError(null)} className="ml-auto p-1 hover:bg-red-100 dark:hover:bg-red-500/20 rounded-lg transition-colors">
                <X className="w-3 h-3 text-red-500" />
              </button>
            </div>
          )}

          {/* Redirect URL */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5 uppercase tracking-wider">Redirect URL</label>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={redirectUrl}
                readOnly
                className="flex-1 px-3 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-xs text-slate-600 dark:text-slate-300 font-mono"
              />
              <button
                onClick={handleCopy}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold transition-all border",
                  copied
                    ? "bg-emerald-50 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/20 text-emerald-600"
                    : "bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-blue-300"
                )}
              >
                {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>

          {/* Client ID */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5 uppercase tracking-wider">Client ID</label>
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder={isEnabled ? '••••••••  (configured — paste to replace)' : 'Paste your Client ID'}
              className="w-full px-3 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-teal-300 dark:focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10 outline-none transition-all"
            />
          </div>

          {/* Client Secret */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5 uppercase tracking-wider">Client Secret</label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder={isEnabled ? '••••••••  (configured — paste to replace)' : 'Paste your Client Secret'}
                className="w-full px-3 py-2.5 pr-10 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-teal-300 dark:focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10 outline-none transition-all"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
              >
                {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* Setup guide */}
          <div className="flex items-start gap-2 p-3 bg-blue-50 dark:bg-blue-500/10 border border-blue-100 dark:border-blue-500/20 rounded-xl">
            <Info className="w-4 h-4 text-blue-500 dark:text-blue-400 shrink-0 mt-0.5" />
            <div className="text-xs text-blue-600 dark:text-blue-400 leading-relaxed">
              <p>{meta.instructions}</p>
              <a
                href={meta.guideUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 mt-1 text-blue-700 dark:text-blue-300 font-bold hover:underline underline-offset-2"
              >
                {meta.guideLabel}
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>

          {/* Save button */}
          <div className="flex justify-end">
            <button
              onClick={handleSave}
              disabled={saving || !clientId.trim() || !clientSecret.trim()}
              className={cn(
                "flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold transition-all",
                saving || !clientId.trim() || !clientSecret.trim()
                  ? "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-not-allowed"
                  : "bg-teal-600 text-white hover:bg-teal-700 shadow-sm shadow-teal-600/15 active:scale-[0.98]"
              )}
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {saving ? 'Enabling…' : isEnabled ? 'Update Credentials' : 'Enable Provider'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
//  Main Auth Providers Tab
// ═══════════════════════════════════════════════════════════

export default function AuthProvidersTab() {
  const [projects, setProjects] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadProjects = useCallback(async () => {
    try {
      const res = await fetch('/api/auth-providers');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProjects(data.projects || []);
      if (data.projects?.length > 0 && !selectedId) {
        setSelectedId(data.projects[0].id);
      }
    } catch (err) {
      setError('Could not load Supabase projects.');
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 text-teal-500 animate-spin" />
        <span className="ml-3 text-sm text-slate-400">Loading auth providers…</span>
      </div>
    );
  }

  if (projects.length === 0) {
    return (
      <div className="space-y-6">
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-soft">
          <div className="px-6 py-5 border-b border-slate-100 dark:border-slate-800">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
                <Database className="w-4 h-4 text-slate-500 dark:text-slate-400" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">Auth Providers</h3>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Configure OAuth for your Supabase projects</p>
              </div>
            </div>
          </div>
          <div className="px-6 py-5">
            <div className="flex flex-col items-center justify-center py-12 border border-dashed border-slate-200 dark:border-slate-700 rounded-xl bg-slate-50/50 dark:bg-slate-800/50">
              <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center mb-3">
                <Shield className="w-5 h-5 text-slate-300 dark:text-slate-600" />
              </div>
              <p className="text-sm font-medium text-slate-400 dark:text-slate-500">No Supabase projects found</p>
              <p className="text-xs text-slate-300 dark:text-slate-600 mt-1">Create a project with &quot;Use Supabase&quot; to configure auth providers</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const selected = projects.find((p) => p.id === selectedId) || projects[0];
  const allProviders = Array.isArray(selected?.suggested_providers) ? selected.suggested_providers : [];
  const enabledMap = {};
  if (Array.isArray(selected?.auth_providers)) {
    for (const p of selected.auth_providers) {
      enabledMap[p.provider] = true;
    }
  }
  // Merge suggested with enabled status
  const providerList = allProviders.map((sp) => ({
    ...sp,
    enabled: enabledMap[sp.provider] || sp.enabled || false,
  }));

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-center gap-3 px-4 py-3 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-xl">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Project selector */}
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-soft">
        <div className="px-6 py-5 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-teal-50 dark:bg-teal-500/10 border border-teal-100 dark:border-teal-500/20 flex items-center justify-center">
              <Database className="w-4 h-4 text-teal-600 dark:text-teal-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">Auth Providers</h3>
              <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Configure OAuth providers for your Supabase projects</p>
            </div>
          </div>
        </div>
        <div className="px-6 py-5">
          {/* Project dropdown */}
          {projects.length > 1 && (
            <div className="mb-5">
              <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5 uppercase tracking-wider">Project</label>
              <select
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-teal-300 focus:ring-2 focus:ring-teal-500/10 outline-none transition-all"
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.project_slug} — {p.supabase_ref}.supabase.co
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Project info badge */}
          <div className="flex items-center gap-3 mb-5 p-3 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-slate-800 dark:text-slate-200 truncate">{selected.project_slug}</p>
              <p className="text-xs text-slate-400 dark:text-slate-500 font-mono mt-0.5">{selected.supabase_ref}.supabase.co</p>
            </div>
            <span className={cn(
              "inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase",
              selected.status === 'ACTIVE'
                ? "bg-emerald-50 dark:bg-emerald-500/10 border-emerald-100 dark:border-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                : selected.status === 'PAUSED'
                  ? "bg-amber-50 dark:bg-amber-500/10 border-amber-100 dark:border-amber-500/20 text-amber-600 dark:text-amber-400"
                  : "bg-slate-50 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-400 dark:text-slate-500"
            )}>
              <span className={cn("w-1.5 h-1.5 rounded-full", selected.status === 'ACTIVE' ? 'bg-emerald-500' : selected.status === 'PAUSED' ? 'bg-amber-500' : 'bg-slate-400')} />
              {selected.status}
            </span>
          </div>

          {/* Email auth always-on banner */}
          <div className="flex items-center gap-3 mb-5 p-3 bg-teal-50 dark:bg-teal-500/10 border border-teal-100 dark:border-teal-500/20 rounded-xl">
            <Check className="w-4 h-4 text-teal-600 dark:text-teal-400 shrink-0" />
            <div>
              <p className="text-sm font-medium text-teal-700 dark:text-teal-300">Email + Magic Link</p>
              <p className="text-xs text-teal-600/70 dark:text-teal-400/70">Always enabled — no configuration needed</p>
            </div>
          </div>

          {/* Provider cards */}
          {providerList.length > 0 ? (
            <div className="space-y-3">
              {providerList.map((p) => (
                <ProviderCard
                  key={p.provider}
                  provider={p}
                  supabaseRef={selected.supabase_ref}
                  supabaseProjectId={selected.id}
                  onSaved={loadProjects}
                />
              ))}
            </div>
          ) : (
            <div className="text-center py-6">
              <p className="text-sm text-slate-400 dark:text-slate-500">No OAuth providers suggested for this project type.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
