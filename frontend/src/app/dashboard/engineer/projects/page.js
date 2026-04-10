'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Apps Page (Base44-style)
//  Clean list-style cards with icon, name, description
// ─────────────────────────────────────────────────────────

import {
  Plus, Clock, Search, MoreHorizontal,
  ExternalLink, Code2, Play, Eye, FolderGit2,
  Grid2X2, List, Star, SlidersHorizontal,
  Trash2, AlertTriangle, X, Loader2,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useWizard } from '../layout';
import CustomSelect from '@/components/ui/CustomSelect';

function formatTime(dateStr) {
  if (!dateStr) return '';
  const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// Emoji icons for projects (deterministic based on name hash)
const PROJECT_EMOJIS = ['🔥', '⚡', '🚀', '💎', '🎯', '🌟', '🎨', '🔮', '🌊', '🍀', '🦊', '🎪'];
const EMOJI_BG_COLORS = [
  'bg-orange-50 dark:bg-orange-500/10',
  'bg-amber-50 dark:bg-amber-500/10',
  'bg-blue-50 dark:bg-blue-500/10',
  'bg-violet-50 dark:bg-violet-500/10',
  'bg-emerald-50 dark:bg-emerald-500/10',
  'bg-rose-50 dark:bg-rose-500/10',
  'bg-cyan-50 dark:bg-cyan-500/10',
  'bg-indigo-50 dark:bg-indigo-500/10',
  'bg-teal-50 dark:bg-teal-500/10',
  'bg-pink-50 dark:bg-pink-500/10',
  'bg-lime-50 dark:bg-lime-500/10',
  'bg-fuchsia-50 dark:bg-fuchsia-500/10',
];

function getProjectHash(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

/* ── Delete Confirm Modal ── */
function DeleteModal({ project, onConfirm, onCancel, loading }) {
  const displayName = (project.projectName || project.repoName || 'Untitled')
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, l => l.toUpperCase())
    .slice(0, 50);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative w-full max-w-md bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between px-6 pt-6 pb-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-500" />
            </div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">Delete project?</h3>
          </div>
          <button onClick={onCancel} className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-6 py-5">
          <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
            This will permanently delete{' '}
            <span className="font-semibold text-slate-700 dark:text-slate-200">{displayName}</span>
            {project.repoUrl && (
              <> and remove the GitHub repository <span className="font-mono text-xs bg-slate-100 dark:bg-white/[0.06] px-1.5 py-0.5 rounded">{project.repoUrl.replace('https://github.com/', '')}</span></>
            )}.
            This cannot be undone.
          </p>
        </div>
        <div className="flex items-center justify-end gap-3 px-6 pb-6">
          <button onClick={onCancel} disabled={loading} className="px-4 py-2.5 text-sm font-semibold text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] rounded-xl border border-slate-200 dark:border-slate-700/50 transition-all disabled:opacity-50">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={loading} className="flex items-center gap-2 px-4 py-2.5 text-sm font-bold text-white bg-red-600 hover:bg-red-700 rounded-xl shadow-sm shadow-red-600/20 transition-all disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            {loading ? 'Deleting…' : 'Delete project'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Project Card (Base44 "Apps" style) ── */
function ProjectCard({ project, onClick, isLaunching, onDeleteClick }) {
  const [showMenu, setShowMenu] = useState(false);
  const hasDeployment = !!project.deployUrl;
  const rawName = project.projectName || project.repoName || 'Untitled';
  const displayName = rawName.replace(/[-_]/g, ' ').replace(/\b\w/g, l => l.toUpperCase()).slice(0, 50);
  const hash = getProjectHash(rawName);
  const emoji = PROJECT_EMOJIS[hash % PROJECT_EMOJIS.length];
  const bgColor = EMOJI_BG_COLORS[hash % EMOJI_BG_COLORS.length];

  return (
    <div className="group bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5 hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all duration-200 cursor-pointer"
      onClick={() => !isLaunching && onClick()}>
      {/* Top row: icon + name + menu */}
      <div className="flex items-start gap-3.5 mb-3">
        <div className={cn("w-11 h-11 rounded-xl flex items-center justify-center shrink-0", bgColor)}>
          <span className="text-lg leading-none">{emoji}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <h3 className="text-[15px] font-semibold text-slate-900 dark:text-white truncate leading-tight">{displayName}</h3>
            <div className="relative shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => setShowMenu(!showMenu)} className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors opacity-0 group-hover:opacity-100">
                <MoreHorizontal className="w-4 h-4" />
              </button>
              {showMenu && (
                <>
                  <div className="fixed inset-0 z-30" onClick={() => setShowMenu(false)} />
                  <div className="absolute right-0 top-full mt-1 z-40 w-44 bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200 dark:border-[#444c56] shadow-xl dark:shadow-black/50 overflow-hidden py-1 animate-scale-in">
                    <button onClick={() => { setShowMenu(false); onClick(); }} className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                      <Play className="w-3.5 h-3.5" /> Open Workspace
                    </button>
                    {hasDeployment && (
                      <a href={project.deployUrl} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                        <Eye className="w-3.5 h-3.5" /> View Live Site
                      </a>
                    )}
                    <button className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                      <Code2 className="w-3.5 h-3.5" /> Export Code
                    </button>
                    <div className="border-t border-slate-100 dark:border-[#2d333b] my-1" />
                    <button
                      onClick={() => { setShowMenu(false); onDeleteClick(project); }}
                      className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10"
                    >
                      <Trash2 className="w-3.5 h-3.5" /> Delete project
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Description */}
      <p className="text-[13px] text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed mb-3.5">
        {project.description || `An AI-generated application based on ${rawName.replace(/[-_]/g, ' ')}.`}
      </p>

      {/* Footer */}
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-500">
        <span className="font-medium">By {project.ownerEmail || 'you'}</span>
        <span className="mx-0.5">·</span>
        <span>Created {formatTime(project.createdAt || project.updatedAt)}</span>
      </div>
    </div>
  );
}

/* ── Loading Skeleton (single card) ── */
function LoadingSkeleton() {
  return (
    <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5">
      <div className="flex items-start gap-3.5 mb-3">
        <div className="w-11 h-11 rounded-xl bg-slate-100 dark:bg-[#21262d] animate-pulse shrink-0" />
        <div className="flex-1 space-y-2.5 pt-1">
          <div className="h-4 bg-slate-100 dark:bg-[#21262d] rounded w-3/5 animate-pulse" />
        </div>
      </div>
      <div className="space-y-2 mb-3.5">
        <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-4/5 animate-pulse" />
        <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-2/3 animate-pulse" />
      </div>
      <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-1/2 animate-pulse" />
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   APPS PAGE (Base44 style)
   ════════════════════════════════════════════════════════ */
export default function ProjectsPage() {
  const router = useRouter();
  const { setShowWizard } = useWizard();
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [isLaunching, setIsLaunching] = useState(false);
  const [viewMode, setViewMode] = useState('grid');
  const [sortBy, setSortBy] = useState('updated');

  // Delete state
  const [deleteTarget, setDeleteTarget] = useState(null); // project to delete
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    fetch('/api/platform-repos').then(r => r.json()).then(d => setProjects(d.repos || [])).catch(() => setProjects([])).finally(() => setLoading(false));
  }, []);

  const handleLaunchProject = (pr) => {
    if (!pr.projectId) return;
    setIsLaunching(true);
    router.push(`/dashboard/engineer/workspace/${pr.projectId}`);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const res = await fetch('/api/delete-project', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ projectId: deleteTarget.projectId, repoUrl: deleteTarget.repoUrl }),
      });
      if (res.ok) {
        setProjects(prev => prev.filter(p => p.projectId !== deleteTarget.projectId));
      }
    } catch {}
    setDeleting(false);
    setDeleteTarget(null);
  };

  const filtered = projects.filter(pr => {
    const name = (pr.projectName || pr.repoName || '').toLowerCase();
    if (search && !name.includes(search.toLowerCase())) return false;
    if (filter === 'live' && !pr.deployUrl) return false;
    if (filter === 'draft' && pr.deployUrl) return false;
    return true;
  });

  const liveCount = projects.filter(p => p.deployUrl).length;
  const draftCount = projects.filter(p => !p.deployUrl).length;

  return (
    <div className="h-full bg-slate-50/50 dark:bg-[#0d1117] overflow-y-auto">
      <div className="px-8 lg:px-10 py-10">

        {/* Delete Confirm Modal */}
        {deleteTarget && (
          <DeleteModal
            project={deleteTarget}
            onConfirm={handleDeleteConfirm}
            onCancel={() => setDeleteTarget(null)}
            loading={deleting}
          />
        )}

        {/* Header */}
        <div className="flex items-start justify-between mb-8">
          <div>
            <h1 className="text-[32px] font-extrabold text-slate-900 dark:text-white tracking-tight">Apps</h1>
          </div>
          <button onClick={() => setShowWizard(true)}
            className="flex items-center gap-2 px-5 py-2.5 text-[13px] font-semibold text-white bg-slate-900 dark:bg-white dark:text-slate-900 rounded-full hover:bg-slate-800 dark:hover:bg-slate-100 shadow-sm transition-all">
            <Plus className="w-4 h-4" /> Create New App
          </button>
        </div>

        {/* Toolbar: Search + Filters + View Toggle */}
        <div className="flex items-center gap-3 mb-8">
          {/* Search */}
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search apps"
              className="w-full pl-10 pr-4 py-2.5 text-[13px] bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl text-slate-700 dark:text-slate-300 placeholder:text-slate-400 outline-none focus:border-orange-300 dark:focus:border-orange-500/40 transition" />
          </div>

          {/* Sort dropdown */}
          <CustomSelect
            value={sortBy}
            onChange={setSortBy}
            className="min-w-[160px]"
            options={[
              { value: 'updated', label: 'Last updated' },
              { value: 'newest', label: 'Newest first' },
              { value: 'alpha', label: 'Alphabetical' },
            ]}
          />

          {/* View toggle */}
          <div className="flex items-center border border-slate-200 dark:border-[#2d333b] rounded-xl overflow-hidden bg-white dark:bg-[#161b22]">
            <button onClick={() => setViewMode('grid')} className={cn("p-2.5 transition-colors", viewMode === 'grid' ? "bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white" : "text-slate-400 hover:text-slate-600")}>
              <Grid2X2 className="w-4 h-4" />
            </button>
            <button onClick={() => setViewMode('list')} className={cn("p-2.5 transition-colors", viewMode === 'list' ? "bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white" : "text-slate-400 hover:text-slate-600")}>
              <List className="w-4 h-4" />
            </button>
            <button className="p-2.5 text-slate-400 hover:text-slate-600 transition-colors">
              <Star className="w-4 h-4" />
            </button>
            <button className="p-2.5 text-slate-400 hover:text-slate-600 transition-colors">
              <SlidersHorizontal className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Content */}
        {loading ? (
          <div className="max-w-sm">
            <LoadingSkeleton />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <FolderGit2 className="w-12 h-12 text-slate-200 dark:text-slate-700 mb-4" />
            <h3 className="text-[16px] font-bold text-slate-800 dark:text-white mb-1">
              {search ? 'No matching apps' : 'No apps yet'}
            </h3>
            <p className="text-[13px] text-slate-400 dark:text-slate-500 mb-6 max-w-sm">
              {search ? `No apps match "${search}".` : 'Create your first AI-powered application to get started.'}
            </p>
            {!search && (
              <button onClick={() => router.push('/dashboard/engineer')}
                className="flex items-center gap-2 px-5 py-2.5 text-[13px] font-semibold text-white bg-slate-900 dark:bg-white dark:text-slate-900 rounded-xl hover:bg-slate-800 dark:hover:bg-slate-100 shadow-sm transition-all">
                <Plus className="w-4 h-4" /> Create App
              </button>
            )}
          </div>
        ) : (
          viewMode === 'list' ? (
            /* ── TABLE VIEW ── */
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] overflow-hidden">
              {/* Table header */}
              <div className="grid grid-cols-[1fr_2fr_1fr_120px_80px] gap-4 px-5 py-3 border-b border-slate-100 dark:border-[#21262d] bg-slate-50/50 dark:bg-white/[0.02]">
                <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Name</span>
                <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Description</span>
                <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Created by</span>
                <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Last updated</span>
                <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</span>
              </div>
              {/* Table rows */}
              {filtered.map((pr) => {
                const rawName = pr.projectName || pr.repoName || 'Untitled';
                const displayName = rawName.replace(/[-_]/g, ' ').replace(/\b\w/g, l => l.toUpperCase()).slice(0, 50);
                const hash = getProjectHash(rawName);
                const emoji = PROJECT_EMOJIS[hash % PROJECT_EMOJIS.length];
                const bgColor = EMOJI_BG_COLORS[hash % EMOJI_BG_COLORS.length];
                return (
                  <div
                    key={pr.projectId}
                    onClick={() => handleLaunchProject(pr)}
                    className="grid grid-cols-[1fr_2fr_1fr_120px_80px] gap-4 px-5 py-4 border-b border-slate-100 dark:border-[#21262d] last:border-b-0 hover:bg-slate-50/50 dark:hover:bg-white/[0.02] transition-colors cursor-pointer group items-center"
                  >
                    {/* Name */}
                    <div className="flex items-center gap-3 min-w-0">
                      <div className={cn("w-9 h-9 rounded-lg flex items-center justify-center shrink-0", bgColor)}>
                        <span className="text-sm leading-none">{emoji}</span>
                      </div>
                      <span className="text-[14px] font-semibold text-slate-900 dark:text-white truncate">{displayName}</span>
                    </div>
                    {/* Description */}
                    <p className="text-[13px] text-slate-500 dark:text-slate-400 truncate">
                      {pr.description || `An AI-generated application based on ${rawName.replace(/[-_]/g, ' ')}.`}
                    </p>
                    {/* Created by */}
                    <span className="text-[13px] text-slate-500 dark:text-slate-400 truncate">
                      {pr.ownerEmail || 'you'}
                    </span>
                    {/* Last updated */}
                    <span className="text-[13px] text-slate-500 dark:text-slate-400">
                      {formatTime(pr.updatedAt || pr.createdAt)}
                    </span>
                    {/* Actions */}
                    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => setDeleteTarget(pr)}
                        className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-all opacity-0 group-hover:opacity-100"
                        title="Delete project"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                );
              })}
              {/* Pagination */}
              {filtered.length > 0 && (
                <div className="flex items-center justify-center gap-3 px-5 py-4 border-t border-slate-100 dark:border-[#21262d]">
                  <button className="px-4 py-1.5 text-[13px] font-medium text-slate-500 dark:text-slate-400 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors">
                    Previous
                  </button>
                  <button className="px-4 py-1.5 text-[13px] font-medium text-slate-500 dark:text-slate-400 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors">
                    Next
                  </button>
                </div>
              )}
            </div>
          ) : (
            /* ── GRID VIEW ── */
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {filtered.map((pr) => (
                <ProjectCard
                  key={pr.projectId}
                  project={pr}
                  onClick={() => handleLaunchProject(pr)}
                  isLaunching={isLaunching}
                  onDeleteClick={setDeleteTarget}
                />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
}
