'use client';

// ─────────────────────────────────────────────────────────
//  ProjectDashboard
//
//  Project Settings surface, rendered inside the workspace's
//  "Settings" tab (internal key still "dashboard" for stability).
//  Sub-nav: General / Users / Domains / Billing / Danger Zone.
//
//  Pure presentational + a few handlers; the parent workspace
//  owns subscription state, project metadata, and rename/delete.
// ─────────────────────────────────────────────────────────

import { useState, useEffect, useCallback } from 'react';
import {
  Search, LayoutGrid, Users as UsersIcon, Database, BarChart3, Megaphone,
  Globe, Plug, Shield, Bot, Zap, FileText, Code2, Settings as SettingsIcon,
  Copy, Check, ExternalLink, Pencil, Star, Share2, ChevronDown,
  Eye, EyeOff, Lock, ArrowRight, Sliders, UserPlus, Diamond, Loader2,
  CreditCard, AlertTriangle, Trash2, Send, Mail, AlertCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';
import { safeJsonFetch } from '@/lib/api/safeFetch';

// Top-level sidebar nav. Renamed and slimmed down to match the workspace
// "Settings" tab spec: General / Users / Billing / Danger Zone.
// Domains kept as a live tab since it's an existing shipped feature.
const TABS = [
  { key: 'general',  label: 'General',     icon: SettingsIcon },
  { key: 'users',    label: 'Users',       icon: UsersIcon    },
  { key: 'domains',  label: 'Domains',     icon: Globe        },
  { key: 'billing',  label: 'Billing',     icon: CreditCard   },
  { key: 'danger',   label: 'Danger Zone', icon: AlertTriangle, danger: true },
];

// ── Tiny helpers ─────────────────────────────────────────
function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        if (!text) return;
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="p-2 rounded-lg border border-slate-200 dark:border-[#2d333b] hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-slate-500"
      title="Copy"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

function PlanLockBadge({ label = 'Starter+' }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400 text-[10px] font-bold uppercase tracking-wider">
      <Diamond className="w-2.5 h-2.5 fill-current" /> {label}
    </span>
  );
}

function timeAgo(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const days = Math.floor(ms / 86_400_000);
  if (days < 1)   return 'today';
  if (days === 1) return '1 day ago';
  if (days < 30)  return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months === 1) return '1 month ago';
  return `${months} months ago`;
}

// ── Sidebar ──────────────────────────────────────────────
function Sidebar({ activeTab, onTabChange }) {
  const [query, setQuery] = useState('');
  const filtered = query
    ? TABS.filter((t) => t.label.toLowerCase().includes(query.toLowerCase()))
    : TABS;

  return (
    <aside className="w-[260px] shrink-0 border-r border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] flex flex-col">
      <div className="px-4 pt-4 pb-2">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Settings</p>
      </div>
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search…"
            className="w-full pl-9 pr-3 py-2 text-[13px] rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] focus:outline-none focus:ring-1 focus:ring-[#dc5426]/40 text-slate-700 dark:text-slate-200"
          />
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        {filtered.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => onTabChange(tab.key)}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] font-medium mb-0.5 transition-colors text-left',
                isActive
                  ? tab.danger
                    ? 'bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-300'
                    : 'bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white'
                  : tab.danger
                    ? 'text-red-600 dark:text-red-400 hover:bg-red-50/50 dark:hover:bg-red-500/5'
                    : 'text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.03]',
              )}
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="flex-1">{tab.label}</span>
              {tab.badge && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                  {tab.badge}
                </span>
              )}
              {tab.dropdown && <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

// ─────────────────────────────────────────────────────────
//  OVERVIEW TAB
// ─────────────────────────────────────────────────────────
function OverviewTab({ project, builtInUrl, onRename, onOpenApp }) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(project.title || 'Untitled project');
  const [renaming, setRenaming] = useState(false);
  const [favorited, setFavorited] = useState(false);
  const [visibility, setVisibility] = useState('public');
  const [requireLogin, setRequireLogin] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [hideBadge, setHideBadge] = useState(false);

  const commitRename = async () => {
    const next = titleDraft.trim();
    if (!next || next === project.title) {
      setEditingTitle(false);
      setTitleDraft(project.title || 'Untitled project');
      return;
    }
    setRenaming(true);
    try {
      await onRename(next);
      setEditingTitle(false);
    } finally {
      setRenaming(false);
    }
  };

  const description = project.description || 'A project built with Lucid AI.';

  return (
    <div className="max-w-5xl mx-auto px-8 py-8">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-emerald-700 to-emerald-900 flex items-center justify-center text-white text-2xl font-bold shrink-0 shadow-sm">
          {(project.title || '?').charAt(0).toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          {editingTitle ? (
            <div className="flex items-center gap-2 mb-1">
              <input
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitRename();
                  if (e.key === 'Escape') { setEditingTitle(false); setTitleDraft(project.title || 'Untitled project'); }
                }}
                disabled={renaming}
                className="text-3xl font-bold bg-transparent border-b-2 border-[#dc5426] focus:outline-none text-slate-900 dark:text-white w-full"
              />
              {renaming && <Loader2 className="w-5 h-5 animate-spin text-slate-400" />}
            </div>
          ) : (
            <button
              onClick={() => { setTitleDraft(project.title || 'Untitled project'); setEditingTitle(true); }}
              className="text-left flex items-center gap-2 group mb-1"
            >
              <h1 className="text-3xl font-bold text-slate-900 dark:text-white truncate">
                {project.title || 'Untitled project'}
              </h1>
              <Pencil className="w-4 h-4 text-slate-400 opacity-60 group-hover:opacity-100" />
            </button>
          )}
          <div className="flex items-start gap-2 mb-1">
            <p className="text-sm text-slate-600 dark:text-slate-400">{description}</p>
            <Pencil className="w-3.5 h-3.5 text-slate-400 mt-0.5 cursor-not-allowed" title="Description editing coming soon" />
          </div>
          <p className="text-xs text-slate-400">Created {timeAgo(project.created_at) || 'recently'}</p>
        </div>
        <button
          onClick={() => setFavorited((v) => !v)}
          className="shrink-0 p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-white/[0.04] text-slate-400"
          title={favorited ? 'Unfavorite' : 'Favorite'}
        >
          <Star className={cn('w-5 h-5', favorited && 'fill-amber-400 text-amber-400')} />
        </button>
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-2 mb-8">
        <button
          onClick={onOpenApp}
          disabled={!builtInUrl}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 dark:border-[#2d333b] text-sm font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.04] disabled:opacity-50 transition-colors bg-white dark:bg-[#161b22]"
        >
          <ExternalLink className="w-4 h-4" /> Open App
        </button>
        <button
          onClick={() => {
            if (!builtInUrl) return;
            navigator.clipboard.writeText(builtInUrl);
            setShareCopied(true);
            setTimeout(() => setShareCopied(false), 1500);
          }}
          disabled={!builtInUrl}
          className="flex flex-col items-center px-4 py-1.5 rounded-xl border border-slate-200 dark:border-[#2d333b] text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.04] disabled:opacity-50 transition-colors bg-white dark:bg-[#161b22]"
        >
          <span className="flex items-center gap-2 font-semibold">
            <Share2 className="w-4 h-4" /> {shareCopied ? 'Link copied!' : 'Share App'}
          </span>
          <span className="text-[11px] text-slate-400 -mt-0.5">win free credits!</span>
        </button>
      </div>

      {/* App Visibility + Invite Users (side by side) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        {/* App Visibility */}
        <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5">
          <h3 className="text-base font-bold text-slate-900 dark:text-white mb-1">App Visibility</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">Control who can access your application</p>

          <div className="relative mb-3">
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value)}
              className="w-full pl-9 pr-9 py-2.5 rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-[13px] text-slate-700 dark:text-slate-200 appearance-none focus:outline-none focus:ring-1 focus:ring-[#dc5426]/40"
            >
              <option value="public">Public</option>
              <option value="private">Private</option>
            </select>
            <Globe className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          <label className="flex items-center gap-2 text-[13px] text-slate-700 dark:text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={requireLogin}
              onChange={(e) => setRequireLogin(e.target.checked)}
              className="w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-[#dc5426] focus:ring-[#dc5426]/40"
            />
            Require login to access
          </label>
        </div>

        {/* Invite Users */}
        <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5 relative">
          <UserPlus className="absolute top-5 right-5 w-4 h-4 text-slate-400" />
          <h3 className="text-base font-bold text-slate-900 dark:text-white mb-1">Invite Users</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">Grow your user base by inviting others</p>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => {
                if (!builtInUrl) return;
                navigator.clipboard.writeText(builtInUrl);
              }}
              className="flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-[13px] font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.04]"
            >
              <Copy className="w-3.5 h-3.5" /> Copy Link
            </button>
            <button className="flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-blue-600 text-white text-[13px] font-semibold hover:bg-blue-700">
              Send Invites
            </button>
          </div>
        </div>
      </div>

      {/* Move to Workspace */}
      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5 mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-base font-bold text-slate-900 dark:text-white">Move to Workspace</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Move this app to another workspace</p>
        </div>
        <button
          disabled
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 dark:border-[#2d333b] text-[13px] font-semibold text-slate-400 cursor-not-allowed"
          title="Workspaces coming soon"
        >
          <ArrowRight className="w-3.5 h-3.5" /> Move App
        </button>
      </div>

      {/* Platform Badge */}
      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5 flex items-center justify-between">
        <div>
          <h3 className="text-base font-bold text-slate-900 dark:text-white">Platform Badge</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            The &quot;Built with Lucid AI&quot; badge is currently {hideBadge ? 'hidden' : 'visible'} on your app.
          </p>
        </div>
        <button
          onClick={() => setHideBadge((v) => !v)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 dark:border-[#2d333b] text-[13px] font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.04]"
        >
          {hideBadge ? <><Eye className="w-3.5 h-3.5" /> Show Badge</> : <><EyeOff className="w-3.5 h-3.5" /> Hide Badge</>}
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  DOMAINS TAB
// ─────────────────────────────────────────────────────────
function DomainsTab({ builtInUrl, canUseCustomDomain, onUpgradeClick, project }) {
  const subdomain = (() => {
    if (!builtInUrl) return null;
    try { return new URL(builtInUrl).hostname; } catch { return builtInUrl; }
  })();

  const senderName = project.title ? `${project.title} App` : 'Your App';

  return (
    <div className="max-w-5xl mx-auto px-8 py-8">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Domains</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-8">
        Buy, connect and manage your domains. <a className="underline hover:text-slate-700 dark:hover:text-slate-300" href="#" onClick={(e) => e.preventDefault()}>Learn more</a>
      </p>

      {/* Built-in URL */}
      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl mb-4 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-[#21262d]">
          <h3 className="text-base font-bold text-slate-900 dark:text-white">Built-in URL</h3>
          <button
            disabled
            className="px-3 py-1.5 rounded-lg border border-slate-200 dark:border-[#2d333b] text-[13px] font-semibold text-slate-400 cursor-not-allowed"
            title="URL editing coming soon"
          >
            Edit URL
          </button>
        </div>
        <div className="p-5">
          {subdomain ? (
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2.5 rounded-lg bg-slate-50 dark:bg-[#0d1117] border border-slate-200 dark:border-[#2d333b] text-[13px] text-slate-700 dark:text-slate-300 truncate">
                {subdomain}
              </code>
              <CopyButton text={builtInUrl} />
            </div>
          ) : (
            <p className="text-sm text-slate-400 italic">Not deployed yet — push or publish your project to get a URL.</p>
          )}
        </div>
      </div>

      {/* Custom domains */}
      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl mb-4 overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-slate-100 dark:border-[#21262d]">
          <h3 className="text-base font-bold text-slate-900 dark:text-white">Custom domains</h3>
          {!canUseCustomDomain && <PlanLockBadge />}
        </div>
        <div className="p-5">
          {canUseCustomDomain ? (
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="yourcompany.com"
                className="flex-1 px-3 py-2.5 rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-[13px] text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-[#dc5426]/40"
              />
              <button className="px-4 py-2.5 rounded-lg bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:opacity-90">
                Connect
              </button>
            </div>
          ) : (
            <div className="text-center py-8 px-4">
              <p className="text-base font-semibold text-slate-700 dark:text-slate-300 mb-1">Get your custom domain</p>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">
                Custom domains are available on the Starter plan and above.
              </p>
              <button
                onClick={onUpgradeClick}
                className="px-5 py-2 rounded-lg bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-[13px] font-bold hover:opacity-90 inline-flex items-center gap-2"
              >
                <Diamond className="w-3.5 h-3.5 fill-current" /> Upgrade your plan
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Email domain */}
      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-[#21262d]">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-bold text-slate-900 dark:text-white">Email domain</h3>
            {!canUseCustomDomain && <PlanLockBadge />}
          </div>
          <button
            disabled={!canUseCustomDomain}
            className={cn(
              'px-3 py-1.5 rounded-lg border text-[13px] font-semibold',
              canUseCustomDomain
                ? 'border-slate-200 dark:border-[#2d333b] text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.04]'
                : 'border-slate-200 dark:border-[#2d333b] text-slate-400 cursor-not-allowed',
            )}
          >
            Use your custom domain
          </button>
        </div>
        <div className="p-5">
          <p className="text-[14px] text-slate-700 dark:text-slate-200 font-medium">no-reply@lucid-ai-apps.com</p>
          <p className="text-[12px] text-slate-500 dark:text-slate-400 mt-1">Sender Name: {senderName}</p>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  USERS TAB (with Users + Pending requests sub-tabs)
// ─────────────────────────────────────────────────────────
function UsersTab({ project }) {
  const projectId = project?.id;       // UUID PK (chat_sessions.id)
  const [members, setMembers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [loading, setLoading] = useState(true);
  const [currentUserId, setCurrentUserId] = useState(null);

  // Invite form
  const [inviteEmail, setInviteEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const isOwner = members.some(
    (m) => m.user_id === currentUserId && m.role === 'owner',
  );

  const fetchAll = useCallback(async () => {
    if (!projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [mRes, iRes] = await Promise.all([
        safeJsonFetch(`/api/projects/${encodeURIComponent(projectId)}/members`).catch(() => ({ members: [] })),
        safeJsonFetch(`/api/projects/${encodeURIComponent(projectId)}/invites`).catch(() => ({ invites: [] })),
      ]);
      setMembers(mRes?.members || []);
      setInvites(iRes?.invites || []);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    (async () => {
      const sb = getSupabaseBrowserClient();
      const { data: { user } } = await sb.auth.getUser();
      setCurrentUserId(user?.id || null);
    })();
    fetchAll();
  }, [fetchAll]);

  const handleInvite = async (e) => {
    e?.preventDefault?.();
    setError(null);
    setSuccess(null);
    const email = inviteEmail.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setError('Please enter a valid email address.');
      return;
    }
    if (!projectId) {
      setError('Project not ready yet — try again in a moment.');
      return;
    }
    setSending(true);
    try {
      const data = await safeJsonFetch(
        `/api/projects/${encodeURIComponent(projectId)}/invites`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        },
      );
      setSuccess(
        data?.delivery === 'user_exists'
          ? `${email} already has an account — they'll see the invitation immediately.`
          : `Invitation sent to ${email}.`,
      );
      setInviteEmail('');
      fetchAll();
    } catch (err) {
      setError(err.message || 'Failed to send invite.');
    } finally {
      setSending(false);
    }
  };

  const handleRevoke = async (inviteId) => {
    try {
      await safeJsonFetch(`/api/invites/${encodeURIComponent(inviteId)}`, { method: 'DELETE' });
      fetchAll();
    } catch (err) {
      setError(err.message || 'Failed to revoke invite.');
    }
  };

  const handleRemove = async (userId) => {
    if (!confirm('Remove this member from the project?')) return;
    try {
      await safeJsonFetch(
        `/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
        { method: 'DELETE' },
      );
      fetchAll();
    } catch (err) {
      setError(err.message || 'Failed to remove member.');
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-8 py-8">
      <div className="flex items-start justify-between mb-6 gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Users</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage project members and invitations.</p>
        </div>
      </div>

      {/* Invite form (owners only) */}
      {isOwner && (
        <form onSubmit={handleInvite} className="mb-6 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-3">Invite a new member</h3>
          <div className="flex items-center gap-2">
            <input
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="teammate@example.com"
              disabled={sending}
              className="flex-1 px-3 py-2.5 text-[13px] rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 text-slate-700 dark:text-slate-200"
            />
            <button
              type="submit"
              disabled={sending || !inviteEmail.trim()}
              className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg bg-blue-600 text-white text-[13px] font-semibold hover:bg-blue-700 disabled:opacity-60"
            >
              {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              Send invitation
            </button>
          </div>
          {error && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5" /> {error}
            </p>
          )}
          {success && (
            <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
              <Check className="w-3.5 h-3.5" /> {success}
            </p>
          )}
        </form>
      )}

      {/* Members list */}
      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl overflow-hidden mb-6">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-[#21262d]">
          <h3 className="text-base font-bold text-slate-900 dark:text-white">Members ({members.length})</h3>
        </div>
        {loading ? (
          <div className="px-5 py-8 flex items-center justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
          </div>
        ) : members.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-slate-500">No members yet.</div>
        ) : (
          members.map((m) => {
            const isSelf = m.user_id === currentUserId;
            const canRemove = isOwner && !isSelf && m.role !== 'owner';
            const label = m.name || m.email || m.user_id;
            return (
              <div key={m.user_id} className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 dark:border-[#21262d] last:border-b-0 hover:bg-slate-50 dark:hover:bg-white/[0.02]">
                <div className="flex items-center gap-3 min-w-0">
                  {m.avatar_url ? (
                    <img src={m.avatar_url} alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
                  ) : (
                    <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-[#21262d] text-slate-500 grid place-items-center text-xs font-semibold shrink-0">
                      {(label || '?').charAt(0).toUpperCase()}
                    </div>
                  )}
                  <div className="min-w-0">
                    <p className="text-[14px] font-semibold text-slate-900 dark:text-white truncate">
                      {label}
                      {isSelf && <span className="ml-1.5 text-xs text-slate-400 font-normal">(you)</span>}
                    </p>
                    {m.email && m.name && (
                      <p className="text-[12px] text-slate-500 dark:text-slate-400 truncate">{m.email}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className={cn(
                    'text-[11px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider',
                    m.role === 'owner'
                      ? 'bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300'
                      : 'bg-slate-100 dark:bg-[#21262d] text-slate-600 dark:text-slate-400',
                  )}>
                    {m.role || 'member'}
                  </span>
                  {canRemove && (
                    <button
                      onClick={() => handleRemove(m.user_id)}
                      className="p-1.5 rounded-md text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-500/10"
                      title="Remove member"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Pending invitations */}
      {invites.length > 0 && (
        <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-[#21262d]">
            <h3 className="text-base font-bold text-slate-900 dark:text-white">Pending invitations ({invites.length})</h3>
          </div>
          {invites.map((i) => (
            <div key={i.invite_id} className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 dark:border-[#21262d] last:border-b-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300 grid place-items-center shrink-0">
                  <Mail className="w-3.5 h-3.5" />
                </div>
                <div className="min-w-0">
                  <p className="text-[14px] font-semibold text-slate-900 dark:text-white truncate">{i.email}</p>
                  <p className="text-[12px] text-slate-500 dark:text-slate-400">Pending · expires {new Date(i.expires_at).toLocaleDateString()}</p>
                </div>
              </div>
              {isOwner && (
                <button
                  onClick={() => handleRevoke(i.invite_id)}
                  className="px-3 py-1.5 rounded-md text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.05]"
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  BILLING TAB — project-level billing summary + link to global billing
// ─────────────────────────────────────────────────────────
function BillingTab({ subscription, onUpgradeClick }) {
  const planKey = subscription?.plan || 'free';
  const planLabel = (planKey[0]?.toUpperCase() || '') + planKey.slice(1);
  return (
    <div className="max-w-3xl mx-auto px-8 py-8">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Billing</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
        Billing is managed at the account level. This project inherits your account plan.
      </p>

      <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-6 mb-4">
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Current plan</p>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{planLabel}</p>
          </div>
          <CreditCard className="w-6 h-6 text-slate-300" />
        </div>
        <button
          onClick={onUpgradeClick}
          className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:opacity-90"
        >
          Manage billing
          <ArrowRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  DANGER ZONE TAB — delete project, transfer ownership
// ─────────────────────────────────────────────────────────
function DangerZoneTab({ project, onDelete }) {
  const [confirmText, setConfirmText] = useState('');
  const [deleting, setDeleting] = useState(false);
  const requiredText = project?.title || 'this project';
  const canDelete = confirmText.trim().toLowerCase() === requiredText.trim().toLowerCase();

  const handleDelete = async () => {
    if (!canDelete || deleting) return;
    setDeleting(true);
    try { await onDelete?.(); } finally { setDeleting(false); }
  };

  return (
    <div className="max-w-3xl mx-auto px-8 py-8">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Danger Zone</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
        Irreversible actions. Proceed with care.
      </p>

      {/* Transfer ownership (placeholder) */}
      <div className="bg-white dark:bg-[#161b22] border border-amber-200/60 dark:border-amber-500/20 rounded-2xl p-5 mb-4">
        <h3 className="text-base font-bold text-slate-900 dark:text-white mb-1">Transfer ownership</h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
          Hand off this project to another member. (Coming soon.)
        </p>
        <button
          disabled
          className="px-4 py-2 rounded-lg border border-slate-200 dark:border-[#2d333b] text-sm font-medium text-slate-400 cursor-not-allowed"
        >
          Transfer ownership
        </button>
      </div>

      {/* Delete project */}
      <div className="bg-white dark:bg-[#161b22] border border-red-200/60 dark:border-red-500/20 rounded-2xl p-5">
        <h3 className="text-base font-bold text-red-700 dark:text-red-400 mb-1">Delete this project</h3>
        <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
          Once you delete a project, there is no going back. All conversations, files, and history will be removed.
        </p>
        <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5">
          Type <span className="font-mono text-slate-700 dark:text-slate-200">{requiredText}</span> to confirm
        </label>
        <input
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder={requiredText}
          className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] focus:outline-none focus:ring-2 focus:ring-red-500/30 focus:border-red-500 mb-3"
        />
        <button
          onClick={handleDelete}
          disabled={!canDelete || deleting}
          className={cn(
            'flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-[13px] font-semibold',
            canDelete && !deleting
              ? 'bg-red-600 text-white hover:bg-red-700'
              : 'bg-slate-100 dark:bg-[#21262d] text-slate-400 cursor-not-allowed',
          )}
        >
          {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          Delete project
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  Locked-tab placeholder
// ─────────────────────────────────────────────────────────
function LockedTab({ name, onUpgradeClick }) {
  return (
    <div className="max-w-2xl mx-auto py-16 text-center">
      <div className="w-12 h-12 rounded-2xl bg-orange-50 dark:bg-orange-500/10 flex items-center justify-center mx-auto mb-4">
        <Lock className="w-6 h-6 text-[#dc5426]" />
      </div>
      <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-2">{name} is coming soon</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
        We&apos;re wiring this up. In the meantime, upgrade to unlock the rest of the platform.
      </p>
      <button
        onClick={onUpgradeClick}
        className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-sm font-bold hover:opacity-90"
      >
        View plans
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  Loading skeleton — shown while the chat_sessions row is being fetched
// ─────────────────────────────────────────────────────────
function DashboardSkeleton() {
  return (
    <div className="max-w-5xl mx-auto px-8 py-8 animate-pulse">
      {/* Header skeleton */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-16 h-16 rounded-2xl bg-slate-200 dark:bg-[#21262d]" />
        <div className="flex-1 space-y-2">
          <div className="h-8 w-2/3 bg-slate-200 dark:bg-[#21262d] rounded-lg" />
          <div className="h-4 w-3/4 bg-slate-100 dark:bg-[#1c2128] rounded" />
          <div className="h-3 w-32 bg-slate-100 dark:bg-[#1c2128] rounded" />
        </div>
      </div>

      {/* Action buttons skeleton */}
      <div className="flex gap-2 mb-8">
        <div className="h-10 w-28 bg-slate-200 dark:bg-[#21262d] rounded-xl" />
        <div className="h-10 w-32 bg-slate-200 dark:bg-[#21262d] rounded-xl" />
      </div>

      {/* Two-card grid skeleton */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div className="h-44 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5 space-y-3">
          <div className="h-5 w-32 bg-slate-200 dark:bg-[#21262d] rounded" />
          <div className="h-3 w-48 bg-slate-100 dark:bg-[#1c2128] rounded" />
          <div className="h-10 w-full bg-slate-100 dark:bg-[#1c2128] rounded-lg mt-4" />
        </div>
        <div className="h-44 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl p-5 space-y-3">
          <div className="h-5 w-32 bg-slate-200 dark:bg-[#21262d] rounded" />
          <div className="h-3 w-48 bg-slate-100 dark:bg-[#1c2128] rounded" />
          <div className="grid grid-cols-2 gap-2 mt-4">
            <div className="h-10 bg-slate-100 dark:bg-[#1c2128] rounded-lg" />
            <div className="h-10 bg-slate-100 dark:bg-[#1c2128] rounded-lg" />
          </div>
        </div>
      </div>

      {/* Wide cards skeleton */}
      <div className="h-20 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl mb-4" />
      <div className="h-20 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl" />
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  Main component
// ─────────────────────────────────────────────────────────
export default function ProjectDashboard({
  project = {},
  builtInUrl = '',
  subscription = null,
  loading = false,
  onRename = async () => {},
  onDelete = async () => {},
  onOpenApp = () => {},
  onUpgradeClick = () => {},
}) {
  const [activeTab, setActiveTab] = useState('general');
  const canUseCustomDomain = subscription?.limits?.canUseCustomDomain ?? false;

  return (
    <div className="flex h-full overflow-hidden bg-[#fafbfc] dark:bg-[#0d1117]">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <main className="flex-1 overflow-y-auto">
        {loading ? (
          <DashboardSkeleton />
        ) : (
          <>
            {activeTab === 'general' && (
              <OverviewTab
                project={project}
                builtInUrl={builtInUrl}
                onRename={onRename}
                onOpenApp={onOpenApp}
              />
            )}
            {activeTab === 'users' && (
              <UsersTab project={project} />
            )}
            {activeTab === 'domains' && (
              <DomainsTab
                builtInUrl={builtInUrl}
                canUseCustomDomain={canUseCustomDomain}
                onUpgradeClick={onUpgradeClick}
                project={project}
              />
            )}
            {activeTab === 'billing' && (
              <BillingTab
                subscription={subscription}
                onUpgradeClick={onUpgradeClick}
              />
            )}
            {activeTab === 'danger' && (
              <DangerZoneTab
                project={project}
                onDelete={onDelete}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
