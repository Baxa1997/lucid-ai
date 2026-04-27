'use client';

// ─────────────────────────────────────────────────────────
//  ProjectDashboard
//
//  Base44-style project management surface, rendered inside
//  the workspace's "Dashboard" tab. Three live sections:
//    • Overview — app info, visibility, invites, badge
//    • Domains  — built-in URL + locked custom domain (Starter+)
//    • Users    — collaborators table + pending invites
//
//  Pure presentational + a few handlers; the parent workspace
//  owns subscription state, project metadata, and rename/delete.
// ─────────────────────────────────────────────────────────

import { useState, useEffect } from 'react';
import {
  Search, LayoutGrid, Users as UsersIcon, Database, BarChart3, Megaphone,
  Globe, Plug, Shield, Bot, Zap, FileText, Code2, Settings as SettingsIcon,
  Copy, Check, ExternalLink, Pencil, Star, Share2, ChevronDown,
  Eye, EyeOff, Lock, ArrowRight, Sliders, UserPlus, Diamond, Loader2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

// Top-level sidebar nav. Active tabs get full styling;
// "coming soon" tabs render a placeholder when clicked.
const TABS = [
  { key: 'overview',    label: 'Overview',       icon: LayoutGrid },
  { key: 'users',       label: 'Users',          icon: UsersIcon  },
  { key: 'data',        label: 'Data',           icon: Database,    locked: true, dropdown: true },
  { key: 'analytics',   label: 'Analytics',      icon: BarChart3,   locked: true, badge: 'Beta' },
  { key: 'social',      label: 'Social content', icon: Megaphone,   locked: true, badge: 'New' },
  { key: 'domains',     label: 'Domains',        icon: Globe },
  { key: 'integrations',label: 'Integrations',   icon: Plug,        locked: true },
  { key: 'security',    label: 'Security',       icon: Shield,      locked: true },
  { key: 'agents',      label: 'Agents',         icon: Bot,         locked: true },
  { key: 'automations', label: 'Automations',    icon: Zap,         locked: true },
  { key: 'logs',        label: 'Logs',           icon: FileText,    locked: true },
  { key: 'api',         label: 'API',            icon: Code2,       locked: true },
  { key: 'settings',    label: 'Settings',       icon: SettingsIcon, locked: true, dropdown: true },
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
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Dashboard</p>
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
                  ? 'bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white'
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
  const [subTab, setSubTab] = useState('users');     // 'users' | 'pending'
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  // Load owner from Supabase auth (the project creator).
  // For v1 the only user is the owner — invitations land in Pending requests
  // once the invite flow is wired.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const sb = getSupabaseBrowserClient();
        const { data: { user } } = await sb.auth.getUser();
        if (cancelled) return;
        if (user) {
          setUsers([{
            id:    user.id,
            name:  user.user_metadata?.full_name || user.user_metadata?.name || (user.email || '').split('@')[0],
            email: user.email,
            role:  'admin',
            isOwner: true,
          }]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const filtered = users.filter((u) => {
    if (roleFilter !== 'all' && u.role !== roleFilter) return false;
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (u.name || '').toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q);
  });

  return (
    <div className="max-w-5xl mx-auto px-8 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-6 gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Users</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage the app&apos;s users and their roles</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="p-2.5 rounded-lg border border-slate-200 dark:border-[#2d333b] hover:bg-slate-50 dark:hover:bg-white/[0.04] text-slate-500"
            title="Filter"
          >
            <Sliders className="w-4 h-4" />
          </button>
          <button className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:opacity-90">
            Invite User
          </button>
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="inline-flex items-center bg-slate-100 dark:bg-[#21262d] rounded-lg p-0.5 mb-6">
        {[
          { key: 'users',   label: 'Users' },
          { key: 'pending', label: 'Pending requests' },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setSubTab(tab.key)}
            className={cn(
              'px-4 h-8 rounded-md text-[13px] font-semibold transition-all',
              subTab === tab.key
                ? 'bg-white dark:bg-[#0d1117] text-slate-900 dark:text-white shadow-sm'
                : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Users sub-tab */}
      {subTab === 'users' && (
        <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-[#21262d] gap-3">
            <h3 className="text-base font-bold text-slate-900 dark:text-white">Users</h3>
            <div className="flex items-center gap-2 flex-1 max-w-md">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by Email or Name"
                  className="w-full pl-9 pr-3 py-2 text-[13px] rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] focus:outline-none focus:ring-1 focus:ring-[#dc5426]/40 text-slate-700 dark:text-slate-200"
                />
              </div>
              <div className="relative">
                <select
                  value={roleFilter}
                  onChange={(e) => setRoleFilter(e.target.value)}
                  className="appearance-none pl-3 pr-8 py-2 text-[13px] rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] focus:outline-none focus:ring-1 focus:ring-[#dc5426]/40 text-slate-700 dark:text-slate-200"
                >
                  <option value="all">all roles</option>
                  <option value="admin">admin</option>
                  <option value="member">member</option>
                  <option value="viewer">viewer</option>
                </select>
                <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
              </div>
            </div>
          </div>

          {/* Table */}
          <div className="grid grid-cols-[1.5fr_1fr_1.5fr] px-5 py-3 bg-slate-50 dark:bg-[#0d1117] border-b border-slate-100 dark:border-[#21262d]">
            <div className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Name</div>
            <div className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Role</div>
            <div className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Email</div>
          </div>

          {loading ? (
            <div className="px-5 py-8 flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-5 py-12 text-center text-sm text-slate-500">No users match your filter.</div>
          ) : (
            filtered.map((u) => (
              <div key={u.id} className="grid grid-cols-[1.5fr_1fr_1.5fr] px-5 py-4 items-center hover:bg-slate-50 dark:hover:bg-white/[0.02]">
                <div>
                  <p className="text-[14px] font-semibold text-slate-900 dark:text-white">{u.name || '—'}</p>
                  {u.isOwner && <p className="text-[12px] text-slate-500 dark:text-slate-400">Owner</p>}
                </div>
                <div className="text-[14px] text-slate-600 dark:text-slate-300">{u.role}</div>
                <div className="text-[14px] text-slate-600 dark:text-slate-300 truncate">{u.email}</div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Pending requests sub-tab */}
      {subTab === 'pending' && (
        <div className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-2xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-[#21262d] gap-3">
            <h3 className="text-base font-bold text-slate-900 dark:text-white">Pending requests</h3>
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
              <input
                placeholder="Search by Email or Name"
                disabled
                className="w-full pl-9 pr-3 py-2 text-[13px] rounded-lg border border-slate-200 dark:border-[#2d333b] bg-slate-50 dark:bg-[#0d1117] text-slate-700 dark:text-slate-200 cursor-not-allowed"
              />
            </div>
          </div>
          <div className="border-2 border-dashed border-slate-200 dark:border-[#2d333b] m-5 rounded-xl py-16 text-center">
            <p className="text-base font-bold text-slate-900 dark:text-white mb-1">No pending requests</p>
            <p className="text-sm text-slate-500 dark:text-slate-400">There are currently no access requests awaiting approval</p>
          </div>
        </div>
      )}
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
  const [activeTab, setActiveTab] = useState('overview');
  const canUseCustomDomain = subscription?.limits?.canUseCustomDomain ?? false;

  const tabDef = TABS.find((t) => t.key === activeTab);

  return (
    <div className="flex h-full overflow-hidden bg-[#fafbfc] dark:bg-[#0d1117]">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <main className="flex-1 overflow-y-auto">
        {loading ? (
          <DashboardSkeleton />
        ) : (
          <>
            {activeTab === 'overview' && (
              <OverviewTab
                project={project}
                builtInUrl={builtInUrl}
                onRename={onRename}
                onOpenApp={onOpenApp}
              />
            )}
            {activeTab === 'domains' && (
              <DomainsTab
                builtInUrl={builtInUrl}
                canUseCustomDomain={canUseCustomDomain}
                onUpgradeClick={onUpgradeClick}
                project={project}
              />
            )}
            {activeTab === 'users' && (
              <UsersTab project={project} />
            )}
            {tabDef?.locked && (
              <LockedTab name={tabDef.label} onUpgradeClick={onUpgradeClick} />
            )}
          </>
        )}
      </main>
    </div>
  );
}
