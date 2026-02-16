'use client';

// ─────────────────────────────────────────────────────────
//  u-code — ProjectWizard Component
//  Smart repo selection with provider integration awareness
// ─────────────────────────────────────────────────────────

import { useState } from 'react';
import { cn } from '@/lib/utils';
import {
  Github, GitBranch, ChevronDown, Check, AlertTriangle,
  Plug, Rocket, Folder, Search, Shield, ShieldAlert,
  ArrowRight, Sparkles, Globe, ChevronRight, X,
} from 'lucide-react';

/**
 * ProjectWizard — Guides the user through selecting a repo,
 * configuring branch strategy, and launching an agent session.
 *
 * Props:
 * @param {Array}    integrations  - Connected integrations [{ id, provider, label }]
 * @param {Array}    repositories  - Available repos [{ id, name, url, provider, defaultBranch }]
 * @param {Function} onLaunch      - Called with { projectId, repoUrl, branch, task, sandboxMode, createBranch }
 * @param {Function} onConnect     - Called when user wants to connect a provider
 * @param {boolean}  loading       - Whether repos are loading
 */
export default function ProjectWizard({
  integrations = [],
  repositories = [],
  onLaunch,
  onConnect,
  loading = false,
}) {
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [showRepoDropdown, setShowRepoDropdown] = useState(false);
  const [repoSearch, setRepoSearch] = useState('');
  const [createBranch, setCreateBranch] = useState(true);
  const [task, setTask] = useState('');
  const [isLaunching, setIsLaunching] = useState(false);

  const hasIntegrations = integrations.length > 0;

  const filteredRepos = repositories.filter(r =>
    r.name.toLowerCase().includes(repoSearch.toLowerCase())
  );

  const handleLaunch = async () => {
    if (!selectedRepo || !task.trim()) return;
    setIsLaunching(true);
    try {
      await onLaunch?.({
        projectId: selectedRepo.id,
        repoUrl: selectedRepo.url,
        branch: createBranch ? `agent/${Date.now()}` : selectedRepo.defaultBranch || 'main',
        task: task.trim(),
        sandboxMode: true,
        createBranch,
      });
    } finally {
      setIsLaunching(false);
    }
  };

  // ═══════════════════════════════════════════════════════
  //  No Integrations — Connect Provider CTA
  // ═══════════════════════════════════════════════════════

  if (!hasIntegrations) {
    return (
      <div className="max-w-xl mx-auto">
        <div className="bg-white rounded-2xl border border-slate-200 p-8 shadow-sm">
          {/* Icon */}
          <div className="flex justify-center mb-6">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-100 flex items-center justify-center">
              <Plug className="w-7 h-7 text-blue-500" />
            </div>
          </div>

          {/* Title */}
          <h2 className="text-xl font-extrabold text-slate-900 text-center mb-2">
            Connect Your Repository
          </h2>
          <p className="text-sm text-slate-400 text-center max-w-sm mx-auto mb-8 leading-relaxed">
            Link your GitHub or GitLab account so the AI agent can
            access your codebase and submit changes.
          </p>

          {/* Buttons */}
          <div className="space-y-3">
            <button
              onClick={() => onConnect?.('github')}
              className="w-full flex items-center justify-center gap-3 px-5 py-3.5 bg-slate-900 text-white rounded-xl font-bold text-[15px] hover:bg-slate-800 transition-all active:scale-[0.98] shadow-sm"
            >
              <Github className="w-5 h-5" />
              Connect GitHub
              <ArrowRight className="w-4 h-4 ml-auto opacity-50" />
            </button>

            <button
              onClick={() => onConnect?.('gitlab')}
              className="w-full flex items-center justify-center gap-3 px-5 py-3.5 bg-white text-slate-700 border border-slate-200 rounded-xl font-bold text-[15px] hover:bg-slate-50 hover:border-slate-300 transition-all active:scale-[0.98]"
            >
              <GitBranch className="w-5 h-5 text-orange-500" />
              Connect GitLab
              <ArrowRight className="w-4 h-4 ml-auto opacity-30" />
            </button>
          </div>

          {/* Divider */}
          <div className="flex items-center gap-4 my-7">
            <div className="flex-1 h-px bg-slate-100" />
            <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-widest">or</span>
            <div className="flex-1 h-px bg-slate-100" />
          </div>

          {/* Demo CTA */}
          <button
            onClick={() => onLaunch?.({ projectId: 'demo', task: 'Explore the demo project', sandboxMode: true })}
            className="w-full flex items-center justify-center gap-2.5 px-5 py-3 text-blue-600 rounded-xl font-semibold text-sm hover:bg-blue-50 transition-all group"
          >
            <Sparkles className="w-4 h-4 text-blue-400 group-hover:text-blue-500 transition-colors" />
            Try a Demo Project
            <ChevronRight className="w-4 h-4 opacity-40 group-hover:translate-x-0.5 transition-transform" />
          </button>
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════
  //  Has Integrations — Repo Selection + Branch Strategy
  // ═══════════════════════════════════════════════════════

  return (
    <div className="max-w-2xl mx-auto">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">

        {/* ── Header ──────────────────────────────── */}
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center">
            <Rocket className="w-5 h-5 text-blue-600" />
          </div>
          <div>
            <h2 className="text-lg font-extrabold text-slate-900">Start a Session</h2>
            <p className="text-xs text-slate-400">Select a repo, describe the task, and launch.</p>
          </div>
        </div>

        {/* ── Connected Providers Badge ────────────── */}
        <div className="flex flex-wrap gap-2 mb-5">
          {integrations.map((integration) => (
            <div
              key={integration.id}
              className="flex items-center gap-2 px-3 py-1.5 bg-green-50 border border-green-200 rounded-full"
            >
              <div className="w-2 h-2 rounded-full bg-green-500" />
              <span className="text-xs font-semibold text-green-700">
                {integration.provider === 'GITHUB' ? 'GitHub' : 'GitLab'}
              </span>
              {integration.label && (
                <span className="text-xs text-green-500">· {integration.label}</span>
              )}
            </div>
          ))}
        </div>

        {/* ── Repository Selector ──────────────────── */}
        <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">
          Repository
        </label>
        <div className="relative mb-5">
          <button
            onClick={() => setShowRepoDropdown(!showRepoDropdown)}
            className={cn(
              "w-full flex items-center justify-between px-4 py-3 rounded-xl border text-sm transition-all",
              showRepoDropdown
                ? "border-blue-300 ring-2 ring-blue-100 bg-white"
                : "border-slate-200 bg-slate-50 hover:border-slate-300"
            )}
          >
            <div className="flex items-center gap-3 truncate">
              {selectedRepo ? (
                <>
                  <Github className="w-4 h-4 text-slate-500 shrink-0" />
                  <span className="font-medium text-slate-800 truncate">{selectedRepo.name}</span>
                </>
              ) : (
                <>
                  <Folder className="w-4 h-4 text-slate-300 shrink-0" />
                  <span className="text-slate-400">Select a repository...</span>
                </>
              )}
            </div>
            <ChevronDown className={cn(
              "w-4 h-4 text-slate-400 shrink-0 transition-transform",
              showRepoDropdown && "rotate-180"
            )} />
          </button>

          {/* Dropdown */}
          {showRepoDropdown && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowRepoDropdown(false)} />
              <div className="absolute top-full left-0 right-0 mt-2 bg-white rounded-xl border border-slate-200 shadow-xl z-50 overflow-hidden">
                {/* Search */}
                <div className="px-3 py-2.5 border-b border-slate-100">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                    <input
                      value={repoSearch}
                      onChange={(e) => setRepoSearch(e.target.value)}
                      className="w-full pl-9 pr-3 py-2 text-sm border border-slate-100 rounded-lg bg-slate-50 outline-none focus:border-blue-300 focus:ring-2 focus:ring-blue-50 text-slate-700"
                      placeholder="Search repositories..."
                      autoFocus
                    />
                  </div>
                </div>
                {/* List */}
                <div className="max-h-56 overflow-y-auto">
                  {loading ? (
                    <div className="px-4 py-8 text-center">
                      <div className="w-5 h-5 border-2 border-blue-200 border-t-blue-600 rounded-full animate-spin mx-auto mb-2" />
                      <p className="text-xs text-slate-400">Loading repos...</p>
                    </div>
                  ) : filteredRepos.length === 0 ? (
                    <div className="px-4 py-6 text-center">
                      <p className="text-sm text-slate-400">No repositories found</p>
                    </div>
                  ) : (
                    filteredRepos.map((repo) => (
                      <button
                        key={repo.id}
                        onClick={() => {
                          setSelectedRepo(repo);
                          setShowRepoDropdown(false);
                          setRepoSearch('');
                        }}
                        className={cn(
                          "w-full flex items-center gap-3 px-4 py-3 text-sm transition-all text-left hover:bg-slate-50",
                          selectedRepo?.id === repo.id && "bg-blue-50"
                        )}
                      >
                        <Github className="w-4 h-4 text-slate-400 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className={cn(
                            "font-medium truncate",
                            selectedRepo?.id === repo.id ? "text-blue-700" : "text-slate-700"
                          )}>
                            {repo.name}
                          </p>
                          {repo.url && (
                            <p className="text-[11px] text-slate-400 truncate">{repo.url}</p>
                          )}
                        </div>
                        {selectedRepo?.id === repo.id && (
                          <Check className="w-4 h-4 text-blue-600 shrink-0" />
                        )}
                      </button>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* ── Branch Strategy (shown when repo selected) ── */}
        {selectedRepo && (
          <div className="mb-5 animate-fade-in">
            <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-3">
              Branch Strategy
            </label>

            {/* Toggle */}
            <button
              onClick={() => setCreateBranch(!createBranch)}
              className={cn(
                "w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border transition-all text-left",
                createBranch
                  ? "border-green-200 bg-green-50"
                  : "border-amber-200 bg-amber-50"
              )}
            >
              {createBranch ? (
                <Shield className="w-5 h-5 text-green-500 shrink-0" />
              ) : (
                <ShieldAlert className="w-5 h-5 text-amber-500 shrink-0" />
              )}

              <div className="flex-1">
                <p className={cn(
                  "text-sm font-semibold",
                  createBranch ? "text-green-700" : "text-amber-700"
                )}>
                  {createBranch ? 'Create new feature branch' : 'Commit directly to main'}
                </p>
                <p className={cn(
                  "text-xs mt-0.5",
                  createBranch ? "text-green-500" : "text-amber-500"
                )}>
                  {createBranch
                    ? 'Safe — changes will be on a separate branch for review'
                    : 'Dangerous — AI will commit directly to your main branch'}
                </p>
              </div>

              {/* Custom toggle switch */}
              <div className={cn(
                "w-10 h-6 rounded-full relative transition-colors shrink-0",
                createBranch ? "bg-green-500" : "bg-amber-400"
              )}>
                <div className={cn(
                  "absolute top-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-all",
                  createBranch ? "left-[18px]" : "left-0.5"
                )} />
              </div>
            </button>

            {/* Warning when committing to main */}
            {!createBranch && (
              <div className="mt-3 flex items-start gap-2.5 px-4 py-3 bg-amber-50 border border-amber-200 rounded-xl animate-fade-in">
                <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-xs font-bold text-amber-700">Warning: Direct commits to Main</p>
                  <p className="text-xs text-amber-600 mt-0.5 leading-relaxed">
                    The AI agent will push commits directly to your <code className="px-1 py-0.5 bg-amber-100 rounded text-amber-800 font-mono text-[10px]">main</code> branch.
                    This cannot be undone easily. Consider using a feature branch instead.
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Task Input ──────────────────────────── */}
        <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">
          Task Description
        </label>
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Describe what you want the AI agent to do..."
          rows={3}
          className="w-full px-4 py-3 text-sm border border-slate-200 rounded-xl bg-slate-50 text-slate-700 placeholder:text-slate-300 outline-none focus:border-blue-300 focus:ring-2 focus:ring-blue-50 resize-none mb-5 transition-all"
        />

        {/* ── Launch Button ───────────────────────── */}
        <button
          onClick={handleLaunch}
          disabled={!selectedRepo || !task.trim() || isLaunching}
          className={cn(
            "w-full py-3.5 rounded-xl font-bold text-[15px] flex items-center justify-center gap-2.5 transition-all duration-300",
            selectedRepo && task.trim() && !isLaunching
              ? "bg-gradient-to-r from-blue-600 to-blue-700 text-white hover:from-blue-700 hover:to-blue-800 shadow-sm shadow-blue-600/20 active:scale-[0.98]"
              : "bg-slate-100 text-slate-400 cursor-not-allowed border border-slate-200"
          )}
        >
          {isLaunching ? (
            <>
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Launching Agent...
            </>
          ) : (
            <>
              <Rocket className="w-4.5 h-4.5" />
              Launch Agent
            </>
          )}
        </button>
      </div>
    </div>
  );
}
