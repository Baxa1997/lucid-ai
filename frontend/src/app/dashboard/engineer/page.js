'use client';

import { 
  GitBranch, Plus, ChevronDown, Check, 
  Github, Rocket, Clock, Sparkles, MessageSquare, 
  ArrowRight, Folder, Search, X,
  CircleDot, Loader2
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
  const [selectedModel, setSelectedModel] = useState('anthropic');
  const [showModelDropdown, setShowModelDropdown] = useState(false);

  // Real repos from integrations
  const [allRepos, setAllRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(true);
  const [branches, setBranches] = useState([]);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [integrations, setIntegrations] = useState({ github: null, gitlab: null });

  // Fetch repos on mount
  useEffect(() => {
    (async () => {
      setReposLoading(true);
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


  const handleLaunch = () => {
    if (!localRepo) return;
    setIsLaunching(true);
    setSelectedRepo(localRepo);
    setSourceBranch(localBranch || 'main');
    setSessionActive(true);
    // Store model config for the workspace page to use
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('lucid_model_provider', selectedModel);
      sessionStorage.removeItem('lucid_custom_api_key');
    }
    setTimeout(() => {
      router.push(`/dashboard/engineer/workspace/${encodeURIComponent(localRepo.name)}`);
    }, 1000);
  };

  const handleNewConversation = () => {
    setSessionActive(true);
    setSelectedRepo({ name: 'scratch-session', lang: '', updated: 'Now', stars: 0 });
    router.push('/dashboard/engineer/workspace/scratch-session');
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
                      <div className="max-h-48 overflow-y-auto">
                        {reposLoading ? (
                          <div className="flex items-center justify-center py-6">
                            <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
                          </div>
                        ) : filteredRepos.length === 0 ? (
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
                        ) : (
                          filteredRepos.map((repo) => (
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
                          ))
                        )}
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

            {/* ── AI Model Selector ── */}
            <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mt-1 mb-2">AI Model</p>

            <div className="space-y-2.5 mb-4">
              {/* Model Dropdown */}
              <div className="relative">
                <button
                  onClick={() => { setShowModelDropdown(!showModelDropdown); setShowRepoDropdown(false); setShowBranchDropdown(false); setShowProviderDropdown(false); }}
                  className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600 transition-all"
                >
                  <div className="flex items-center gap-2.5">
                    {selectedModel === 'google' ? (
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none">
                        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
                        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                      </svg>
                    ) : (
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none">
                        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="#D97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    )}
                    <span className="font-medium">
                      {selectedModel === 'google' ? 'Gemini 1.5 Pro' : 'Claude 3.5 Sonnet'}
                    </span>
                    <span className="text-[10px] text-slate-400 ml-0.5">
                      {selectedModel === 'google' ? 'Best for large files' : 'Best for reasoning'}
                    </span>
                  </div>
                  <ChevronDown className={cn("w-4 h-4 text-slate-400 shrink-0 transition-transform", showModelDropdown && "rotate-180")} />
                </button>

                {showModelDropdown && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowModelDropdown(false)} />
                    <div className="absolute right-0 top-full mt-1.5 w-full bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden z-50 shadow-lg">
                      {/* Google Gemini */}
                      <button
                        onClick={() => { setSelectedModel('google'); setShowModelDropdown(false); }}
                        className={cn("w-full flex items-center gap-2.5 px-3.5 py-3 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors", selectedModel === 'google' && "bg-blue-50 dark:bg-blue-500/10")}
                      >
                        <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
                          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
                          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                        </svg>
                        <div className="flex-1">
                          <span className="font-semibold text-slate-900 dark:text-slate-100">Gemini 3 Flash Preview</span>
                          <span className="text-[10px] text-slate-500">Fast inference · Latest preview model</span>
                        </div>
                        {selectedModel === 'google' && <Check className="w-3.5 h-3.5 text-blue-600 shrink-0" />}
                      </button>
                      {/* Anthropic Claude */}
                      <button
                        onClick={() => { setSelectedModel('anthropic'); setShowModelDropdown(false); }}
                        className={cn("w-full flex items-center gap-2.5 px-3.5 py-3 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors border-t border-slate-100 dark:border-slate-700", selectedModel === 'anthropic' && "bg-blue-50 dark:bg-blue-500/10")}
                      >
                        <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
                          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="#D97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                        <div className="flex-1">
                          <div className="font-medium text-slate-700 dark:text-slate-200">Claude 3.5 Sonnet</div>
                          <div className="text-[11px] text-slate-400">Best for reasoning · Superior code quality</div>
                        </div>
                        {selectedModel === 'anthropic' && <Check className="w-3.5 h-3.5 text-blue-600 shrink-0" />}
                      </button>
                    </div>
                  </>
                )}
              </div>

              {/* Custom API Key Input Removed - Using Environment Variable */}
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

          {/* RIGHT: Start from Scratch */}
          <div className="bg-white dark:bg-slate-900  rounded-2xl border border-slate-200 dark:border-slate-800 p-6 shadow-soft flex flex-col">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-8 h-8 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center">
                <Plus className="w-4 h-4 text-violet-600 dark:text-violet-400" />
              </div>
              <h2 className="text-[15px] font-bold text-slate-900 dark:text-slate-100">Start from Scratch</h2>
            </div>
            <p className="text-sm text-slate-400 leading-relaxed flex-1">
              Start a new conversation that is not connected to an existing repository. Perfect for quick experiments, boilerplates, or prototyping new ideas.
            </p>

            {/* New Conversation Button */}
            <button
              onClick={handleNewConversation}
              className="w-full mt-6 py-3 rounded-xl font-bold text-sm flex items-center justify-center gap-2 bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-600/20 transition-all active:scale-[0.98]"
            >
              New Conversation
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* ── Recent Projects ── */}
        {/* <div>
          <h3 className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-5">
            Recent Projects
          </h3>
          <div className="flex items-center gap-3 py-4">
            <div className="w-9 h-9 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
              <Clock className="w-4 h-4 text-slate-300 dark:text-slate-600" />
            </div>
            <span className="text-sm text-slate-400 dark:text-slate-500 italic">No recent conversations</span>
          </div>
        </div> */}
      </div>

    </div>
  );
}
