'use client';

import { 
  Github, Search, Star, Clock, Zap,
  Link as LinkIcon, ArrowRight, ArrowLeft, Play, ChevronDown,
  GitBranch, Sparkles, Loader2, ExternalLink, Beaker
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import useFlowStore from '@/store/useFlowStore';

/* GitLab SVG icon */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

export default function ProjectsPage() {
  const router = useRouter();
  const { 
    integrations, setIntegration, hasAnyIntegration,
    activeProvider, setActiveProvider,
    selectedRepo, setSelectedRepo,
    mockRepos
  } = useFlowStore();

  const [searchQuery, setSearchQuery] = useState('');
  const [isConnecting, setIsConnecting] = useState(false);
  const [showDemoNotice, setShowDemoNotice] = useState(false);

  const hasIntegration = hasAnyIntegration();

  // Filtered repos
  const filteredRepos = useMemo(() => {
    const repos = mockRepos[activeProvider] || [];
    if (!searchQuery.trim()) return repos;
    return repos.filter(r => r.name.toLowerCase().includes(searchQuery.toLowerCase()));
  }, [activeProvider, searchQuery, mockRepos]);

  const handleConnectProvider = (provider) => {
    setIsConnecting(true);
    setTimeout(() => {
      setIntegration(provider, true);
      setActiveProvider(provider);
      setIsConnecting(false);
    }, 1500);
  };

  const handleTryDemo = () => {
    setActiveProvider('demo');
    setShowDemoNotice(true);
    setTimeout(() => setShowDemoNotice(false), 3000);
  };

  const handleRepoSelect = (repo) => {
    setSelectedRepo(repo);
    setTimeout(() => router.push('/dashboard/engineer'), 400);
  };

  const providers = [
    { key: 'github', label: 'GitHub', icon: <Github className="w-4 h-4" />, connected: integrations.github },
    { key: 'gitlab', label: 'GitLab', icon: <GitLabIcon className="w-4 h-4" />, connected: integrations.gitlab },
    { key: 'demo', label: 'Demo Projects', icon: <Beaker className="w-4 h-4" />, connected: true },
  ];

  return (
    <div className="min-h-screen bg-[#f0f4f9] relative overflow-hidden">
      
      {/* Background decoration */}
      <div className="absolute top-0 inset-x-0 h-64 bg-gradient-to-b from-blue-50/50 to-transparent pointer-events-none" />

      {/* ── Top Bar ── */}
      <header className="relative z-20 h-18 py-4 px-8 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <button onClick={() => router.push('/dashboard/engineer')} className="flex items-center gap-2 group px-3 py-1.5 rounded-lg hover:bg-white/50 transition-all">
            <ArrowLeft className="w-4 h-4 text-slate-500 group-hover:-translate-x-0.5 transition-transform" />
            <span className="text-sm font-semibold text-slate-600">Back</span>
          </button>
          
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-blue-700 rounded-lg flex items-center justify-center shadow-sm">
              <Zap className="w-4 h-4 text-white fill-current" />
            </div>
            <span className="font-extrabold text-slate-900 text-sm tracking-tight">Lucid AI</span>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 bg-emerald-50 border border-emerald-100 rounded-full">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs font-bold text-emerald-700 uppercase tracking-wide">System Ready</span>
        </div>
      </header>

      {/* ── Main Content ── */}
      <main className="relative z-10 max-w-4xl mx-auto px-8 py-8">
        
        {/* Title */}
        <div className="mb-10 text-center animate-slide-up">
          <h1 className="text-3xl font-extrabold text-slate-900 tracking-tight mb-2">Select a Project</h1>
          <p className="text-slate-500 font-medium">Choose a repository to start an AI engineering session.</p>
        </div>

        {/* ═══ SCENARIO A: No Integrations ═══ */}
        {!hasIntegration ? (
          <div className="animate-slide-up max-w-2xl mx-auto" style={{ animationDelay: '0.1s' }}>
            
            {/* Connect Git Provider Card */}
            <div className="bg-white rounded-2xl border border-slate-200 p-10 text-center mb-6 shadow-soft">
              <div className="w-16 h-16 rounded-2xl bg-blue-50 border border-blue-100 flex items-center justify-center mx-auto mb-6 shadow-sm">
                <LinkIcon className="w-7 h-7 text-blue-600" />
              </div>
              <h2 className="text-xl font-bold text-slate-900 mb-2">Connect Your Git Provider</h2>
              <p className="text-slate-500 mb-8 max-w-md mx-auto text-sm leading-relaxed">
                Link your GitHub or GitLab account to import repositories and start building with AI.
              </p>

              <div className="flex gap-4 justify-center mb-6">
                <button
                  onClick={() => handleConnectProvider('github')}
                  disabled={isConnecting}
                  className="flex items-center gap-2.5 px-6 py-3 bg-[#24292f] text-white font-bold rounded-xl shadow-lg shadow-slate-900/10 transition-all hover:-translate-y-0.5 disabled:opacity-60 disabled:cursor-not-allowed disabled:transform-none text-sm group"
                >
                  {isConnecting ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> Connecting...</>
                  ) : (
                    <><Github className="w-4.5 h-4.5" /> Connect GitHub</>
                  )}
                </button>
                <button
                  onClick={() => handleConnectProvider('gitlab')}
                  disabled={isConnecting}
                  className="flex items-center gap-2.5 px-6 py-3 bg-[#fc6d26] text-white font-bold rounded-xl shadow-lg shadow-orange-500/10 transition-all hover:-translate-y-0.5 disabled:opacity-60 disabled:cursor-not-allowed disabled:transform-none text-sm"
                >
                  <GitLabIcon className="w-4.5 h-4.5" /> Connect GitLab
                </button>
              </div>

              {/* Demo Project Button */}
              <div className="pt-6 border-t border-slate-50">
                <div className="inline-flex flex-col items-center gap-3">
                  <span className="text-[10px] uppercase tracking-widest font-bold text-slate-400">Or explore without connecting</span>
                  <button
                    onClick={handleTryDemo}
                    className="flex items-center gap-2 px-5 py-2.5 bg-white border border-slate-200 hover:border-blue-200 hover:bg-blue-50 rounded-xl text-sm font-semibold text-slate-600 hover:text-blue-600 transition-all group shadow-sm"
                  >
                    <Play className="w-4 h-4 text-emerald-500 group-hover:scale-110 transition-transform" />
                    Try a Demo Project
                    <ArrowRight className="w-4 h-4 opacity-50 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all" />
                  </button>
                </div>
              </div>
            </div>

            {/* Demo Success Notice */}
            {showDemoNotice && (
              <div className="fixed top-24 right-8 bg-white rounded-xl border border-emerald-200 px-5 py-3 flex items-center gap-3 z-50 animate-slide-down shadow-lg">
                <div className="w-6 h-6 rounded-full bg-emerald-100 flex items-center justify-center">
                  <Sparkles className="w-3 h-3 text-emerald-600" />
                </div>
                <span className="text-sm font-medium text-slate-700">Demo sandbox loaded!</span>
              </div>
            )}
          </div>
        ) : (
          /* ═══ SCENARIO B: Integrations Exist ═══ */
          <div className="animate-slide-up" style={{ animationDelay: '0.1s' }}>
            
            {/* Provider Tabs */}
            <div className="flex justify-center mb-8">
              <div className="flex items-center gap-1 p-1.5 bg-white border border-slate-200 rounded-xl shadow-sm">
                {providers.map((p) => {
                  const isActive = activeProvider === p.key;
                  const isDisabled = !p.connected && p.key !== 'demo';
                  return (
                    <button
                      key={p.key}
                      onClick={() => !isDisabled && setActiveProvider(p.key)}
                      disabled={isDisabled}
                      className={cn(
                        "flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-bold transition-all duration-300",
                        isActive
                          ? "bg-blue-600 text-white shadow-md"
                          : isDisabled
                            ? "text-slate-300 cursor-not-allowed"
                            : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"
                      )}
                    >
                      {p.icon}
                      {p.label}
                      {isDisabled && (
                        <span className="hidden sm:inline-block ml-2 text-[9px] font-bold text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded-full">Not connected</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Content Card */}
            <div className="bg-white rounded-2xl border border-slate-200 shadow-soft overflow-hidden">
              {/* Search */}
              <div className="p-4 border-b border-slate-100 bg-slate-50/50">
                <div className="relative">
                  <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search repositories..."
                    className="w-full pl-11 pr-4 py-3 bg-white border border-slate-200 rounded-xl text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all font-medium shadow-sm"
                  />
                </div>
              </div>

              {/* Repository List */}
              <div className="divide-y divide-slate-100">
                {filteredRepos.map((repo) => (
                  <button
                    key={repo.name}
                    onClick={() => handleRepoSelect(repo)}
                    className={cn(
                      "w-full flex items-center gap-4 p-5 hover:bg-slate-50 transition-all duration-300 text-left group relative",
                      selectedRepo?.name === repo.name ? "bg-blue-50/50" : ""
                    )}
                  >
                    {/* Repo Icon */}
                    <div className={cn(
                      "w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-all shadow-sm",
                      repo.isDemo
                        ? "bg-emerald-50 border border-emerald-100"
                        : "bg-slate-50 border border-slate-100"
                    )}>
                      {repo.isDemo ? (
                        <Beaker className="w-5 h-5 text-emerald-600" />
                      ) : activeProvider === 'github' ? (
                        <Github className="w-5 h-5 text-slate-600" />
                      ) : (
                        <GitLabIcon className="w-5 h-5 text-orange-600" />
                      )}
                    </div>

                    {/* Repo Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2.5">
                        <span className="text-sm font-bold text-slate-900 group-hover:text-blue-600 transition-colors truncate">
                          {repo.name}
                        </span>
                        {repo.isDemo && (
                          <span className="text-[10px] font-bold text-emerald-600 bg-emerald-50 border border-emerald-100 px-2 py-0.5 rounded-full uppercase tracking-wider">
                            Sandbox
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-4 mt-1.5">
                        <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
                          <div className={cn(
                            "w-2 h-2 rounded-full",
                            repo.lang === 'TypeScript' ? 'bg-blue-500' :
                            repo.lang === 'Go' ? 'bg-cyan-500' :
                            repo.lang === 'Python' ? 'bg-amber-500' :
                            repo.lang === 'Java' ? 'bg-red-500' :
                            repo.lang === 'Terraform' ? 'bg-violet-500' :
                            'bg-emerald-500'
                          )} />
                          {repo.lang}
                        </span>
                        <div className="w-1 h-1 rounded-full bg-slate-200" />
                        {repo.stars > 0 && (
                          <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1">
                            <Star className="w-3 h-3" /> {repo.stars}
                          </span>
                        )}
                        <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1">
                          <Clock className="w-3 h-3" /> {repo.updated}
                        </span>
                      </div>
                    </div>

                    {/* Arrow */}
                    <div className="w-8 h-8 rounded-full bg-transparent group-hover:bg-white border border-transparent group-hover:border-slate-200 flex items-center justify-center transition-all">
                      <ArrowRight className="w-4 h-4 text-slate-300 group-hover:text-blue-600 transition-colors" />
                    </div>
                  </button>
                ))}

                {filteredRepos.length === 0 && (
                  <div className="text-center py-12">
                    <Search className="w-10 h-10 text-slate-200 mx-auto mb-3" />
                    <p className="text-sm text-slate-400">No repositories found matching</p>
                    <p className="text-sm font-bold text-slate-700 mt-0.5">&quot;{searchQuery}&quot;</p>
                  </div>
                )}
              </div>
            </div>

            {/* Connect more providers */}
            {(!integrations.github || !integrations.gitlab) && (
              <div className="mt-8 text-center">
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-4">Connect other providers</p>
                <div className="flex justify-center gap-3">
                  {!integrations.github && (
                    <button
                      onClick={() => handleConnectProvider('github')}
                      className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 hover:border-slate-300 rounded-lg text-xs font-bold text-slate-600 hover:text-slate-800 transition-all shadow-sm"
                    >
                      <Github className="w-3.5 h-3.5" /> Connect GitHub
                    </button>
                  )}
                  {!integrations.gitlab && (
                    <button
                      onClick={() => handleConnectProvider('gitlab')}
                      className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 hover:border-slate-300 rounded-lg text-xs font-bold text-slate-600 hover:text-slate-800 transition-all shadow-sm"
                    >
                      <GitLabIcon className="w-3.5 h-3.5 text-orange-500" /> Connect GitLab
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

      </main>
    </div>
  );
}
