'use client';

import { 
  GitBranch, Plus, ChevronDown, Check, 
  Github, Rocket, Clock, Sparkles, MessageSquare, 
  ArrowRight, Folder, Search, X,
  CircleDot, Loader2, Layers, ExternalLink
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import useFlowStore from '@/store/useFlowStore';
import {
  getIntegrations,
  fetchGitHubRepos,
  fetchGitHubBranches,
  fetchGitLabRepos,
  fetchGitLabBranches,
} from '@/lib/integrations';
import { createConversation } from '@/lib/conversations';
import { useWizard } from './layout';

export default function EngineerDashboardPage() {
  const router = useRouter();
  const {
    selectedRepo, setSelectedRepo,
    sourceBranch, setSourceBranch,
    setSessionActive,
  } = useFlowStore();

  const [showRepoDropdown, setShowRepoDropdown] = useState(false);
  const [showBranchDropdown, setShowBranchDropdown] = useState(false);
  const [showProviderDropdown, setShowProviderDropdown] = useState(false);
  const [repoSearch, setRepoSearch] = useState('');
  const [localRepo, setLocalRepo] = useState(null);
  const [localBranch, setLocalBranch] = useState('');
  const [selectedProvider, setSelectedProvider] = useState('github');
  const [isLaunching, setIsLaunching] = useState(false);
  const [showBanner, setShowBanner] = useState(true);

  // Platform-generated repos ("My Projects")
  const [platformRepos, setPlatformRepos] = useState([]);
  const [platformLoading, setPlatformLoading] = useState(true);
  const { setShowWizard } = useWizard();

  // Real repos from integrations
  const [allRepos, setAllRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(true);
  const [branches, setBranches] = useState([]);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [integrations, setIntegrations] = useState({ github: null, gitlab: null });

  // Fetch repos + platform projects on mount
  useEffect(() => {
    (async () => {
      setReposLoading(true);
      setPlatformLoading(true);

      // Fetch platform repos ("My Projects") in parallel
      fetch('/api/platform-repos')
        .then(r => r.json())
        .then(d => setPlatformRepos(d.repos || []))
        .catch(() => setPlatformRepos([]))
        .finally(() => setPlatformLoading(false));

      const intg = await getIntegrations();
      setIntegrations(intg);

      const repos = [];

      // GitHub repos
      if (intg.github?.connected && intg.github?.token) {
        const ghRepos = await fetchGitHubRepos(intg.github.token);
        repos.push(...ghRepos);
      }

      // GitLab repos
      if (intg.gitlab?.connected && intg.gitlab?.token) {
        const glRepos = await fetchGitLabRepos(intg.gitlab.host, intg.gitlab.token);
        repos.push(...glRepos);
      }

      setAllRepos(repos);
      setReposLoading(false);
    })();
  }, []);

  // Fetch branches when a repo is selected
  const loadBranches = useCallback(async (repo) => {
    if (!repo) return;
    setBranchesLoading(true);
    setBranches([]);
    let branchList = [];

    if (repo.provider === 'github' && integrations.github?.token) {
      branchList = await fetchGitHubBranches(integrations.github.token, repo.name);
    } else if (repo.provider === 'gitlab' && integrations.gitlab?.token) {
      branchList = await fetchGitLabBranches(integrations.gitlab.host, integrations.gitlab.token, repo.name);
    }

    setBranches(branchList.length > 0 ? branchList : [repo.defaultBranch || 'main']);
    setBranchesLoading(false);
  }, [integrations]);

  const filteredRepos = allRepos.filter(r => 
    r.name.toLowerCase().includes(repoSearch.toLowerCase())
  );


  const handleLaunch = async () => {
    if (!localRepo) return;
    setIsLaunching(true);
    setSelectedRepo(localRepo);
    setSourceBranch(localBranch || localRepo.defaultBranch || 'main');
    setSessionActive(true);

    // Create conversation in Supabase
    const conversation = await createConversation({
      repoName: localRepo.name,
      repoProvider: localRepo.provider,
      repoUrl: localRepo.url || '',
      branch: localBranch || localRepo.defaultBranch || 'main',
      title: 'New Conversation',
    });

    if (conversation) {
      router.push(`/dashboard/engineer/workspace/${conversation.id}`);
    } else {
      // Fallback to old URL-based routing
      router.push(`/dashboard/engineer/workspace/${encodeURIComponent(localRepo.name)}`);
    }
  };

  // Launch from a platform-generated project ("My Projects")
  const handleLaunchPlatformRepo = async (platformRepo) => {
    if (!platformRepo.projectId) return;
    setIsLaunching(true);

    // Ensure a conversation record exists for this platform project
    // (projects created via the wizard may not have one yet)
    try {
      const existing = await getConversation(platformRepo.projectId);
      if (!existing) {
        await createConversation({
          repoName: platformRepo.repoName || '',
          repoProvider: 'github',
          repoUrl: platformRepo.repoUrl || '',
          branch: 'main',
          title: platformRepo.projectName || platformRepo.repoName || 'Project',
        });
      }
    } catch (e) {
      // Non-critical — workspace will still work via chat_sessions
      console.warn('Could not ensure conversation record:', e);
    }

    router.push(`/dashboard/engineer/workspace/${platformRepo.projectId}`);
  };

  const handleNewConversation = () => {
    setShowWizard(true);
  };

  return (
    <div className="h-full bg-[#f0f4f9] dark:bg-[#0d1117] relative flex flex-col transition-colors duration-200">

      {/* ── Main Content ── */}
      <div className="flex-1 w-full overflow-y-auto">

        {/* ── Hero Section ── */}
        <div className="px-6 lg:px-8 pt-12 pb-10">
          <div className="max-w-[900px] mx-auto">
          
            {/* Banner */}
            {showBanner && (
              <div className="flex items-center justify-center mb-8">
                <div className="flex items-center gap-2 px-5 py-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full shadow-soft">
                  <span className="text-sm text-slate-500 dark:text-slate-400">New around here? Not sure where to start?</span>
                  <button className="text-sm text-slate-800 dark:text-slate-200 font-bold underline underline-offset-2 hover:text-blue-600 dark:hover:text-blue-400 transition-colors">Click here</button>
                </div>
              </div>
            )}
            
            {/* Title */}
            <div className="text-center mb-3">
              <h1 className="text-4xl sm:text-[44px] font-extrabold text-slate-900 dark:text-slate-100 tracking-tight leading-tight">
                Let&apos;s Start Building!
              </h1>
            </div>

            {/* Subtitle */}
            <div className="text-center mb-10">
              <p className="text-[15px] text-slate-400 dark:text-slate-500 max-w-xl mx-auto leading-relaxed">
                Select a repository to begin an autonomous engineering session or start a fresh environment from scratch.
              </p>
            </div>

            {/* ── Two Cards ── */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 relative z-30">
          
          {/* LEFT: Open Repository */}
          <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-6 shadow-soft relative z-30">
            <div className="flex items-center gap-3 mb-1">
              <div className="w-8 h-8 rounded-lg bg-blue-50 dark:bg-blue-500/10 border border-blue-100 dark:border-blue-500/20 flex items-center justify-center">
                <GitBranch className="w-4 h-4 text-blue-600 dark:text-blue-400" />
              </div>
              <h2 className="text-[15px] font-bold text-slate-900 dark:text-slate-100">Open Repository</h2>
            </div>
            
            {/* Select URL label */}
            <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mt-4 mb-3">Select or insert a URL</p>

            <div className="space-y-2.5 mb-4">
              {/* Provider Dropdown */}
              <div className="relative">
                <button
                  onClick={() => { setShowProviderDropdown(!showProviderDropdown); setShowRepoDropdown(false); setShowBranchDropdown(false); }}
                  className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600 transition-all"
                >
                  <div className="flex items-center gap-2.5">
                    <Github className="w-4 h-4 text-slate-700 dark:text-slate-300" />
                    <span className="font-medium">{selectedProvider === 'github' ? 'GitHub' : 'GitLab'}</span>
                  </div>
                  <ChevronDown className={cn("w-4 h-4 text-slate-400 transition-transform", showProviderDropdown && "rotate-180")} />
                </button>

                {showProviderDropdown && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowProviderDropdown(false)} />
                    <div className="absolute right-0 top-full mt-1.5 w-full bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden z-50 shadow-lg">
                      <button
                        onClick={() => { setSelectedProvider('github'); setShowProviderDropdown(false); }}
                        className={cn("w-full flex items-center gap-2.5 px-3.5 py-2.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors", selectedProvider === 'github' && "bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400")}
                      >
                        <Github className="w-4 h-4" /> GitHub
                        {selectedProvider === 'github' && <Check className="w-3.5 h-3.5 ml-auto" />}
                      </button>
                      <button
                        onClick={() => { setSelectedProvider('gitlab'); setShowProviderDropdown(false); }}
                        className={cn("w-full flex items-center gap-2.5 px-3.5 py-2.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors", selectedProvider === 'gitlab' && "bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400")}
                      >
                        <GitBranch className="w-4 h-4" /> GitLab
                        {selectedProvider === 'gitlab' && <Check className="w-3.5 h-3.5 ml-auto" />}
                      </button>
                    </div>
                  </>
                )}
              </div>

              {/* Repository Dropdown */}
              <div className="relative">
                <button
                  onClick={() => { setShowRepoDropdown(!showRepoDropdown); setShowBranchDropdown(false); setShowProviderDropdown(false); }}
                  className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-600 dark:text-slate-300 hover:border-blue-300 dark:hover:border-blue-500/50 transition-all"
                >
                  <div className="flex items-center gap-2.5 truncate">
                    <Folder className="w-4 h-4 text-slate-400 shrink-0" />
                    <span className={cn("truncate", localRepo ? "text-slate-700 font-medium" : "text-slate-400")}>
                      {localRepo ? localRepo.name : 'user/repo'}
                    </span>
                  </div>
                  <ChevronDown className={cn("w-4 h-4 text-slate-400 shrink-0 transition-transform", showRepoDropdown && "rotate-180")} />
                </button>

                {showRepoDropdown && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowRepoDropdown(false)} />
                    <div className="absolute top-full left-0 right-0 mt-1.5 bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden z-50 shadow-lg">
                      <div className="px-3 py-2 border-b border-slate-100 dark:border-slate-700">
                        <div className="relative">
                          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                          <input
                            value={repoSearch}
                            onChange={(e) => setRepoSearch(e.target.value)}
                            className="w-full pl-8 pr-3 py-1.5 text-xs border border-slate-100 dark:border-slate-600 rounded-lg bg-slate-50 dark:bg-slate-700 outline-none focus:border-blue-300 dark:focus:border-blue-500 text-slate-700 dark:text-slate-200"
                            placeholder="Search repositories..."
                            autoFocus
                          />
                        </div>
                      </div>
                      <div className="max-h-64 overflow-y-auto">
                        {/* ── My Projects (Platform repos) ── */}
                        {platformRepos.length > 0 && (
                          <>
                            <div className="px-3 py-1.5 bg-violet-50 dark:bg-violet-500/5 border-b border-violet-100 dark:border-violet-500/10">
                              <span className="text-[10px] font-bold text-violet-500 dark:text-violet-400 uppercase tracking-wider flex items-center gap-1.5">
                                <Layers className="w-3 h-3" />
                                My Projects
                              </span>
                            </div>
                            {platformRepos
                              .filter(p => !repoSearch || p.projectName.toLowerCase().includes(repoSearch.toLowerCase()) || p.repoName.toLowerCase().includes(repoSearch.toLowerCase()))
                              .map((pr) => (
                              <button
                                key={pr.projectId}
                                onClick={() => { setShowRepoDropdown(false); handleLaunchPlatformRepo(pr); }}
                                className="w-full flex items-center gap-3 px-3.5 py-2.5 text-sm transition-all text-left hover:bg-violet-50 dark:hover:bg-violet-500/5 text-slate-600 dark:text-slate-300"
                              >
                                <div className="w-5 h-5 rounded bg-violet-100 dark:bg-violet-500/15 flex items-center justify-center shrink-0">
                                  <Sparkles className="w-3 h-3 text-violet-500" />
                                </div>
                                <div className="flex-1 min-w-0">
                                  <span className="font-medium truncate block text-slate-700 dark:text-slate-200">{pr.projectName}</span>
                                  <span className="text-[10px] text-slate-400 dark:text-slate-500 truncate block">{pr.repoName}</span>
                                </div>
                                <ArrowRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-600 shrink-0" />
                              </button>
                            ))}
                            <div className="border-b border-slate-100 dark:border-slate-700" />
                          </>
                        )}

                        {/* ── Connected repos ── */}
                        {reposLoading ? (
                          <div className="flex items-center justify-center py-6">
                            <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
                          </div>
                        ) : filteredRepos.length === 0 && platformRepos.length === 0 ? (
                          <div className="py-6 text-center">
                            <p className="text-xs text-slate-400 dark:text-slate-500">
                              {allRepos.length === 0 ? 'No integrations connected' : 'No matching repositories'}
                            </p>
                            {allRepos.length === 0 && (
                              <button
                                onClick={() => { setShowRepoDropdown(false); router.push('/dashboard/engineer/integrations'); }}
                                className="text-xs text-blue-600 dark:text-blue-400 font-medium mt-1 hover:underline"
                              >
                                Connect GitHub or GitLab →
                              </button>
                            )}
                          </div>
                        ) : filteredRepos.length > 0 ? (
                          <>
                            {platformRepos.length > 0 && (
                              <div className="px-3 py-1.5 bg-slate-50 dark:bg-slate-800 border-b border-slate-100 dark:border-slate-700">
                                <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Connected Repos</span>
                              </div>
                            )}
                            {filteredRepos.map((repo) => (
                              <button
                                key={`${repo.provider}-${repo.name}`}
                                onClick={() => { setLocalRepo(repo); setLocalBranch(''); setShowRepoDropdown(false); setRepoSearch(''); loadBranches(repo); }}
                                className={cn(
                                  "w-full flex items-center gap-3 px-3.5 py-2.5 text-sm transition-all text-left hover:bg-slate-50 dark:hover:bg-slate-700",
                                  localRepo?.name === repo.name ? "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400" : "text-slate-600 dark:text-slate-300"
                                )}
                              >
                                {repo.provider === 'github'
                                  ? <Github className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                                  : <GitBranch className="w-3.5 h-3.5 text-orange-400 shrink-0" />
                                }
                                <div className="flex-1 min-w-0">
                                  <span className="font-medium truncate block">{repo.name}</span>
                                  {repo.language && <span className="text-[10px] text-slate-400">{repo.language}</span>}
                                </div>
                                {repo.private && <span className="text-[9px] px-1.5 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded text-slate-400 font-medium">Private</span>}
                                {localRepo?.name === repo.name && <Check className="w-3.5 h-3.5 ml-auto text-blue-600 shrink-0" />}
                              </button>
                            ))}
                          </>
                        ) : null}
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* Branch Selector */}
              <div className="relative">
                <button
                  onClick={() => { setShowBranchDropdown(!showBranchDropdown); setShowRepoDropdown(false); setShowProviderDropdown(false); }}
                  className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-600 dark:text-slate-300 hover:border-blue-300 dark:hover:border-blue-500/50 transition-all"
                >
                  <div className="flex items-center gap-2.5">
                    <GitBranch className="w-4 h-4 text-slate-400" />
                    <span className={cn(localBranch ? "text-slate-700 font-medium" : "text-slate-400")}>{localBranch || 'Select branch...'}</span>
                  </div>
                  <ChevronDown className={cn("w-4 h-4 text-slate-400 shrink-0 transition-transform", showBranchDropdown && "rotate-180")} />
                </button>

                {showBranchDropdown && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowBranchDropdown(false)} />
                    <div className="absolute top-full left-0 right-0 mt-1.5 bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden z-50 shadow-lg max-h-48 overflow-y-auto">
                      {branchesLoading ? (
                        <div className="flex items-center justify-center py-6">
                          <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
                        </div>
                      ) : branches.length === 0 ? (
                        <div className="py-6 text-center text-xs text-slate-400 dark:text-slate-500">
                          {localRepo ? 'No branches found' : 'Select a repository first'}
                        </div>
                      ) : (
                      branches.map((branch) => (
                        <button
                          key={branch}
                          onClick={() => { setLocalBranch(branch); setShowBranchDropdown(false); }}
                            className={cn(
                              "w-full flex items-center gap-3 px-3.5 py-2.5 text-sm transition-all text-left hover:bg-slate-50 dark:hover:bg-slate-700",
                              localBranch === branch ? "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400" : "text-slate-600 dark:text-slate-300"
                            )}
                        >
                          <GitBranch className="w-3.5 h-3.5 text-slate-400" />
                          <span className="font-medium">{branch}</span>
                          {localBranch === branch && <Check className="w-3.5 h-3.5 ml-auto text-blue-600" />}
                        </button>
                      ))
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Launch Button */}
            <button
              onClick={handleLaunch}
              disabled={!localRepo || isLaunching}
              className={cn(
                "w-full py-2.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 transition-all duration-300",
                localRepo && !isLaunching
                  ? "bg-gradient-to-r from-blue-600 to-blue-700 text-white hover:from-blue-700 hover:to-blue-800 shadow-sm shadow-blue-600/15"
                  : "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-not-allowed border border-slate-200 dark:border-slate-700"
              )}
            >
              {isLaunching ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Launching...
                </>
              ) : (
                'Launch'
              )}
            </button>
          </div>

          {/* RIGHT: New Project (Wizard) */}
          <div className="bg-gradient-to-br from-violet-50 via-white to-blue-50 dark:from-slate-900 dark:via-slate-900 dark:to-slate-900 rounded-2xl border-2 border-violet-200 dark:border-violet-500/20 p-6 shadow-soft relative overflow-hidden flex flex-col">
            {/* Subtle glow accent */}
            <div className="absolute -top-20 -right-20 w-40 h-40 bg-violet-200/40 dark:bg-violet-500/5 rounded-full blur-3xl pointer-events-none" />
            <div className="flex items-center gap-3 mb-3">
              <div className="w-8 h-8 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center">
                <Sparkles className="w-4 h-4 text-violet-600 dark:text-violet-400" />
              </div>
              <h2 className="text-[15px] font-bold text-slate-900 dark:text-slate-100">New Project</h2>
            </div>
            <p className="text-sm text-slate-400 leading-relaxed flex-1">
              Set up a brand-new project with the guided wizard. Pick your stack, describe your idea, and let AI generate fully production-ready code.
            </p>

            {/* New Project Button */}
            <button
              onClick={handleNewConversation}
              disabled={isLaunching}
              className={cn(
                "w-full mt-6 py-3 rounded-xl font-bold text-sm flex items-center justify-center gap-2 transition-all active:scale-[0.98]",
                isLaunching
                  ? "bg-slate-100 dark:bg-slate-800 text-slate-400 cursor-not-allowed"
                  : "bg-gradient-to-r from-violet-600 to-blue-600 text-white hover:from-violet-700 hover:to-blue-700 shadow-sm shadow-violet-600/20"
              )}
            >
              {isLaunching ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Creating...
                </>
              ) : (
                <>
                  New Project
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div> {/* end RIGHT card */}
          </div> {/* end cards grid */}
          </div> {/* end max-w wrapper */}
        </div> {/* end hero section */}

        {/* ── Your Projects — Discover Grid ── */}
        <div className="px-6 lg:px-8 pb-8">
          <div className="border-t border-slate-200/60 dark:border-slate-800/40 pt-6">
          <div className="flex items-center justify-between mb-5">
            <div>
              <h2 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">
                Your Projects
              </h2>
              <p className="text-sm text-slate-400 dark:text-slate-500 mt-0.5">
                Your AI-generated apps and websites
              </p>
            </div>
            {platformRepos.length > 6 && (
              <button className="text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 px-4 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg hover:bg-white dark:hover:bg-slate-800 transition-all">
                View all
              </button>
            )}
          </div>

          {platformLoading ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="rounded-xl bg-slate-100 dark:bg-[#161b22] border border-slate-200 dark:border-slate-700/50 overflow-hidden animate-pulse">
                  <div className="aspect-[16/9] bg-slate-200 dark:bg-slate-800" />
                  <div className="p-3.5 space-y-2">
                    <div className="h-4 bg-slate-200 dark:bg-slate-700/50 rounded w-2/3" />
                    <div className="h-3 bg-slate-200 dark:bg-slate-700/50 rounded w-2/5" />
                  </div>
                </div>
              ))}
            </div>
          ) : platformRepos.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 px-6 rounded-2xl border-2 border-dashed border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/30">
              <div className="w-14 h-14 rounded-2xl bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center mb-4">
                <Sparkles className="w-6 h-6 text-violet-500" />
              </div>
              <h3 className="text-base font-bold text-slate-800 dark:text-slate-200 mb-1">No projects yet</h3>
              <p className="text-sm text-slate-400 dark:text-slate-500 mb-4">Create your first AI-powered project with the wizard</p>
              <button
                onClick={handleNewConversation}
                className="px-5 py-2 bg-violet-600 text-white text-sm font-bold rounded-xl hover:bg-violet-700 transition-colors shadow-sm"
              >
                New Project
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {platformRepos.slice(0, 12).map((pr, idx) => {
                const GRADIENTS = [
                  'from-[#0f0c29] via-[#302b63] to-[#24243e]',
                  'from-[#0d1b2a] via-[#1b263b] to-[#415a77]',
                  'from-[#1a1a2e] via-[#16213e] to-[#0f3460]',
                  'from-[#141e30] via-[#243b55] to-[#141e30]',
                  'from-[#0c0c1d] via-[#1a1a3e] to-[#2d1b69]',
                  'from-[#1b1b2f] via-[#1a2a4a] to-[#162447]',
                  'from-[#0d0d0d] via-[#1a1a2e] to-[#3a0ca3]',
                  'from-[#1f1c2c] via-[#928dab] to-[#1f1c2c]',
                  'from-[#0f2027] via-[#203a43] to-[#2c5364]',
                ];
                const ACCENTS = [
                  'text-violet-400', 'text-blue-400', 'text-cyan-400',
                  'text-sky-400', 'text-purple-400', 'text-indigo-400',
                  'text-teal-400', 'text-slate-300', 'text-emerald-400',
                ];
                const gradient = GRADIENTS[idx % GRADIENTS.length];
                const accent = ACCENTS[idx % ACCENTS.length];
                const words = (pr.projectName || 'P').replace(/[-_]/g, ' ').split(/\s+/).filter(Boolean);
                const initials = words.length >= 2
                  ? (words[0][0] + words[1][0]).toUpperCase()
                  : words[0].slice(0, 2).toUpperCase();

                return (
                  <div
                    key={pr.projectId}
                    className="group relative rounded-xl bg-white dark:bg-[#161b22] border border-slate-200/80 dark:border-slate-700/40 overflow-hidden hover:border-slate-300 dark:hover:border-slate-600 hover:shadow-xl dark:hover:shadow-2xl dark:hover:shadow-black/30 transition-all duration-300 cursor-pointer"
                  >
                    {/* Thumbnail — gradient + initials */}
                    <button
                      onClick={() => handleLaunchPlatformRepo(pr)}
                      disabled={isLaunching}
                      className="block w-full text-left"
                    >
                      <div className="aspect-[2/1] relative overflow-hidden">
                        <div className={`absolute inset-0 bg-gradient-to-br ${gradient} flex items-center justify-center`}>
                          {/* Decorative light spots */}
                          <div className="absolute inset-0 opacity-[0.07]" style={{
                            backgroundImage: 'radial-gradient(circle at 20% 50%, rgba(255,255,255,0.3) 0%, transparent 50%), radial-gradient(circle at 80% 20%, rgba(255,255,255,0.2) 0%, transparent 40%), radial-gradient(circle at 60% 80%, rgba(255,255,255,0.15) 0%, transparent 45%)',
                          }} />
                          {/* Grid pattern */}
                          <div className="absolute inset-0 opacity-[0.03]" style={{
                            backgroundImage: 'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
                            backgroundSize: '32px 32px',
                          }} />
                          {/* Initials */}
                          <span className={`text-3xl font-black tracking-wider ${accent} select-none drop-shadow-lg`}>
                            {initials}
                          </span>
                          {/* Hover overlay */}
                          <div className="absolute inset-0 bg-white/0 group-hover:bg-white/[0.03] transition-colors duration-300" />
                        </div>
                      </div>
                    </button>

                    {/* Card info + Preview */}
                    <div className="px-3.5 py-3 flex items-center gap-3">
                      <button
                        onClick={() => handleLaunchPlatformRepo(pr)}
                        disabled={isLaunching}
                        className="flex items-center gap-2.5 min-w-0 flex-1"
                      >
                        <div className="w-7 h-7 rounded-full bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center shrink-0">
                          <Sparkles className="w-3.5 h-3.5 text-violet-500" />
                        </div>
                        <div className="min-w-0 flex-1 text-left">
                          <p className="text-[13px] font-bold text-slate-800 dark:text-slate-100 truncate leading-tight">
                            {pr.projectName}
                          </p>
                          <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate leading-tight mt-0.5">
                            {pr.repoName}
                          </p>
                        </div>
                      </button>
                      {pr.deployUrl && (
                        <a
                          href={pr.deployUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 text-[11px] font-bold text-emerald-700 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-500/20 transition-colors"
                        >
                          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                          Preview
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        </div>
      </div>

    </div>
  );
}


