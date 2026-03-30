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
    setIsLaunching(true);

    // Navigate directly to the existing conversation
    if (platformRepo.projectId) {
      router.push(`/dashboard/engineer/workspace/${platformRepo.projectId}`);
    }
  };

  const handleNewConversation = () => {
    setShowWizard(true);
  };

  return (
    <div className="h-full bg-[#f0f4f9] dark:bg-[#0d1117] relative flex flex-col transition-colors duration-200">

      {/* ── Main Content ── */}
      <div className="max-w-3xl mx-auto px-8 py-8 flex-1 flex flex-col justify-center w-full">

        {/* Banner */}
        {showBanner && (
          <div className="flex items-center justify-center mb-6">
            <div className="flex items-center gap-2 px-5 py-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full shadow-soft">
              <span className="text-sm text-slate-500 dark:text-slate-400">New around here? Not sure where to start?</span>
              <button className="text-sm text-slate-800 dark:text-slate-200 font-bold underline underline-offset-2 hover:text-blue-600 dark:hover:text-blue-400 transition-colors">Click here</button>
            </div>
            {/* <button onClick={() => setShowBanner(false)} className="ml-3 text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors">
              <X className="w-4 h-4" />
            </button> */}
          </div>
        )}
        
        {/* Title */}
        <div className="text-center mb-2">
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
        <div className="grid grid-cols-1 md:grid-cols-[400px_400px] justify-center gap-5 mb-14 relative z-30">
          
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
          </div>
        </div>

        {/* ── Recent Projects ── */}
        <div>
          <h3 className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-4">
            Recent Projects
          </h3>
          {platformLoading ? (
            <div className="flex items-center gap-3 py-4">
              <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
              <span className="text-sm text-slate-400 dark:text-slate-500">Loading projects...</span>
            </div>
          ) : platformRepos.length === 0 ? (
            <div className="flex items-center gap-3 py-4">
              <div className="w-9 h-9 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
                <Clock className="w-4 h-4 text-slate-300 dark:text-slate-600" />
              </div>
              <span className="text-sm text-slate-400 dark:text-slate-500 italic">No recent projects — create one with the wizard!</span>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
              {platformRepos.slice(0, 6).map((pr) => (
                <button
                  key={pr.projectId}
                  onClick={() => handleLaunchPlatformRepo(pr)}
                  disabled={isLaunching}
                  className="group flex flex-col gap-2 p-4 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 hover:border-violet-300 dark:hover:border-violet-500/30 hover:shadow-md transition-all text-left"
                >
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center shrink-0">
                      <Sparkles className="w-3.5 h-3.5 text-violet-500 dark:text-violet-400" />
                    </div>
                    <span className="text-sm font-bold text-slate-700 dark:text-slate-200 truncate">
                      {pr.projectName}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-auto">
                    <span className="text-[10px] text-slate-400 dark:text-slate-500 truncate">
                      {pr.repoName}
                    </span>
                    <ArrowRight className="w-3 h-3 text-slate-300 dark:text-slate-600 ml-auto opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                  </div>
                  {pr.deployUrl && (
                    <div className="flex items-center gap-1.5 mt-1">
                      <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                      <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium truncate">
                        Deployed
                      </span>
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

    </div>
  );
}
