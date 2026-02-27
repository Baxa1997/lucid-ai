'use client';

import {
  ChevronRight, Globe, Github, Star, Clock, Code2, Sparkles,
  Monitor, Server, Link2, CheckCircle2, ArrowRight, ArrowLeft, Search,
  X, Info, Check, Loader2
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import useFlowStore from '@/store/useFlowStore';

/* ════════════════════════════════════════════════
   GitLab SVG Icon
   ════════════════════════════════════════════════ */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

/* ════════════════════════════════════════════════
   Lucid AI Logo
   ════════════════════════════════════════════════ */
function LucidLogo() {
  return (
    <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-400 to-blue-600 flex items-center justify-center shadow-sm">
      <Sparkles className="w-4 h-4 text-white" />
    </div>
  );
}

/* ════════════════════════════════════════════════
   STEP INDICATOR
   ════════════════════════════════════════════════ */
function StepIndicator({ currentStep }) {
  const steps = [
    { id: 1, label: 'Source Selection' },
    { id: 2, label: 'AI Analysis' },
    { id: 3, label: 'Generation' },
  ];

  const getStepStatus = (stepId) => {
    if (currentStep === 'choose-source') {
      return stepId === 1 ? 'active' : 'upcoming';
    }
    if (['choose-provider', 'choose-repo', 'enter-url'].includes(currentStep)) {
      if (stepId === 1) return 'completed';
      return stepId === 2 ? 'upcoming' : 'upcoming';
    }
    if (currentStep === 'generating') {
      if (stepId <= 1) return 'completed';
      if (stepId === 2) return 'active';
      return 'upcoming';
    }
    return 'upcoming';
  };

  return (
    <div className="flex items-center justify-center gap-0">
      {steps.map((step, idx) => {
        const status = getStepStatus(step.id);
        return (
          <div key={step.id} className="flex items-center">
            <div className="flex flex-col items-center gap-2">
              <div className={cn(
                "w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold transition-all duration-500",
                status === 'completed' && "bg-gradient-to-br from-cyan-400 to-teal-500 text-white shadow-md shadow-teal-400/25",
                status === 'active' && "bg-gradient-to-br from-cyan-400 to-teal-500 text-white shadow-md shadow-teal-400/25",
                status === 'upcoming' && "bg-slate-100 text-slate-400 border border-slate-200"
              )}>
                {status === 'completed' ? (
                  <Check className="w-4 h-4" />
                ) : (
                  step.id
                )}
              </div>
              <span className={cn(
                "text-xs font-semibold whitespace-nowrap transition-colors",
                status === 'completed' && "text-teal-600",
                status === 'active' && "text-teal-600",
                status === 'upcoming' && "text-slate-400"
              )}>
                {step.label}
              </span>
            </div>
            {idx < steps.length - 1 && (
              <div className={cn(
                "w-24 h-[2px] mx-3 mb-6 transition-colors duration-500",
                getStepStatus(step.id) === 'completed' ? "bg-teal-400" : "bg-slate-200"
              )} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ════════════════════════════════════════════════
   MAIN DOCS PAGE
   ════════════════════════════════════════════════ */
export default function DocsPage() {
  const router = useRouter();
  const { mockRepos } = useFlowStore();

  // Wizard state
  const [step, setStep] = useState('choose-source');
  const [selectedSource, setSelectedSource] = useState(null);
  const [selectedProvider, setSelectedProvider] = useState(null);
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [webUrl, setWebUrl] = useState('');
  const [generationProgress, setGenerationProgress] = useState(0);
  const [generationStep, setGenerationStep] = useState('');

  // Breadcrumb label
  const getBreadcrumb = () => {
    if (step === 'choose-source') return null;
    if (step === 'enter-url') return 'Web Generation';
    if (step === 'choose-provider') return selectedSource === 'frontend' ? 'Frontend' : 'Backend';
    if (step === 'choose-repo') return `${selectedProvider === 'github' ? 'GitHub' : 'GitLab'} Repos`;
    if (step === 'generating') return 'Generating';
    return null;
  };

  // Handle source card click
  const handleSourceSelect = (sourceId) => {
    setSelectedSource(sourceId);
    if (sourceId === 'weburl') {
      setStep('enter-url');
    } else {
      setStep('choose-provider');
    }
  };

  const handleProviderSelect = (provider) => {
    setSelectedProvider(provider);
    setStep('choose-repo');
  };

  const handleRepoSelect = (repo) => {
    setSelectedRepo(repo);
    startGeneration(repo.name);
  };

  const handleWebUrlGenerate = () => {
    if (!webUrl.trim()) return;
    startGeneration(webUrl);
  };

  const startGeneration = (source) => {
    setStep('generating');
    setGenerationProgress(0);

    const steps = [
      { progress: 15, label: 'Connecting to source...' },
      { progress: 30, label: 'Scanning file structure...' },
      { progress: 50, label: 'Analyzing code patterns...' },
      { progress: 70, label: 'Generating documentation...' },
      { progress: 85, label: 'Building navigation tree...' },
      { progress: 100, label: 'Finalizing...' },
    ];

    let i = 0;
    const interval = setInterval(() => {
      if (i < steps.length) {
        setGenerationProgress(steps[i].progress);
        setGenerationStep(steps[i].label);
        i++;
      } else {
        clearInterval(interval);
        setTimeout(() => {
          router.push('/dashboard/docs');
        }, 600);
      }
    }, 700);
  };

  const handleCancel = () => {
    setStep('choose-source');
    setSelectedSource(null);
    setSelectedProvider(null);
    setSelectedRepo(null);
    setSearchQuery('');
    setWebUrl('');
    setGenerationProgress(0);
    setGenerationStep('');
  };

  const handleBack = () => {
    if (step === 'choose-provider') {
      setStep('choose-source');
      setSelectedSource(null);
    } else if (step === 'choose-repo') {
      setStep('choose-provider');
      setSelectedProvider(null);
      setSelectedRepo(null);
      setSearchQuery('');
    } else if (step === 'enter-url') {
      setStep('choose-source');
      setSelectedSource(null);
      setWebUrl('');
    }
  };

  // Filter repos
  const repos = selectedProvider ? (mockRepos[selectedProvider] || []) : [];
  const filteredRepos = searchQuery
    ? repos.filter(r => r.name.toLowerCase().includes(searchQuery.toLowerCase()))
    : repos;

  return (
    <div className="h-full flex flex-col bg-white dark:bg-slate-950 relative overflow-hidden transition-colors duration-200">

      {/* ── Gradient accent line at top ── */}
      <div className="h-[3px] bg-gradient-to-r from-cyan-400 via-blue-500 to-violet-500 shrink-0" />

      {/* ── Top Navigation Bar ── */}
      <div className="h-14 border-b border-slate-100 dark:border-slate-800 flex items-center px-6 justify-between shrink-0 bg-white dark:bg-slate-950 z-20 transition-colors duration-200">
        <div className="flex items-center gap-3">
          {step !== 'choose-source' && step !== 'generating' && (
            <button
              onClick={handleBack}
              className="w-8 h-8 rounded-lg border border-slate-200 dark:border-slate-700 flex items-center justify-center text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 transition-all mr-1"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
          )}
          {getBreadcrumb() && (
            <>
              <span className="text-sm text-slate-300 dark:text-slate-600 font-medium">Sources</span>
              <ChevronRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-600" />
              <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">{getBreadcrumb()}</span>
            </>
          )}
        </div>
        {step !== 'choose-source' && step !== 'generating' && (
          <button
            onClick={handleCancel}
            className="text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
          >
            Cancel
          </button>
        )}
      </div>

      {/* ── Main Content ── */}
      <div className="flex-1 overflow-y-auto relative">
        {/* Background grid */}
        <div className="absolute inset-0 bg-dot-grid opacity-30 dark:opacity-10 pointer-events-none" />

        {/* ══════════════════════════════════════════
            STEP 1: Choose Source
           ══════════════════════════════════════════ */}
        {step === 'choose-source' && (
          <div className="relative flex flex-col items-center justify-center min-h-full px-6 py-16 animate-fade-in">
            {/* Badge */}
            <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-cyan-50 to-teal-50 dark:from-cyan-500/10 dark:to-teal-500/10 border border-cyan-200 dark:border-cyan-500/20 rounded-full mb-6">
              <Sparkles className="w-4 h-4 text-teal-500 dark:text-teal-400" />
              <span className="text-xs font-bold text-teal-700 dark:text-teal-400 uppercase tracking-wider">Choose Your Source</span>
            </div>

            <h1 className="text-4xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight text-center mb-3">
              Generate Documentation
            </h1>
            <p className="text-slate-500 dark:text-slate-400 text-center max-w-lg leading-relaxed mb-12">
              Select a source type and our AI will analyze, crawl, and auto-generate 
              comprehensive technical documentation in seconds.
            </p>

            {/* Source Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5 max-w-3xl w-full">
              {[
                {
                  id: 'frontend',
                  label: 'Frontend',
                  desc: 'Repository code, components & UI',
                  icon: Monitor,
                  gradient: 'from-blue-500 to-cyan-500',
                  bg: 'bg-blue-50',
                  border: 'border-blue-200',
                  text: 'text-blue-600',
                },
                {
                  id: 'backend',
                  label: 'Backend',
                  desc: 'API services, DB schemas & logic',
                  icon: Server,
                  gradient: 'from-violet-500 to-purple-600',
                  bg: 'bg-violet-50',
                  border: 'border-violet-200',
                  text: 'text-violet-600',
                },
                {
                  id: 'weburl',
                  label: 'Web URL',
                  desc: 'Crawl any live website URL',
                  icon: Globe,
                  gradient: 'from-emerald-500 to-teal-500',
                  bg: 'bg-emerald-50',
                  border: 'border-emerald-200',
                  text: 'text-emerald-600',
                },
              ].map((opt) => {
                const Icon = opt.icon;
                return (
                  <button
                    key={opt.id}
                    onClick={() => handleSourceSelect(opt.id)}
                    className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-6 text-center transition-all duration-300 group hover:shadow-card hover:-translate-y-1 relative overflow-hidden"
                  >
                    <div className={cn("absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r opacity-0 group-hover:opacity-100 transition-opacity duration-300", opt.gradient)} />
                    <div className={cn(
                      "w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4 border transition-all duration-300",
                      opt.bg, opt.border, opt.text,
                      "group-hover:scale-110 group-hover:shadow-lg"
                    )}>
                      <Icon className="w-7 h-7" />
                    </div>
                    <h3 className="text-base font-bold text-slate-900 dark:text-slate-100 mb-1">{opt.label}</h3>
                    <p className="text-xs text-slate-400 dark:text-slate-500 leading-relaxed">{opt.desc}</p>
                    <div className="mt-4 pt-3 border-t border-slate-100 dark:border-slate-800 flex items-center justify-center gap-1.5">
                      <span className="text-xs font-semibold text-slate-400 dark:text-slate-500 group-hover:text-slate-600 dark:group-hover:text-slate-300 transition-colors">Get started</span>
                      <ChevronRight className="w-3.5 h-3.5 text-slate-300 group-hover:translate-x-0.5 group-hover:text-slate-500 transition-all" />
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════
            STEP: Choose Provider (GitHub / GitLab)
           ══════════════════════════════════════════ */}
        {step === 'choose-provider' && (
          <div className="relative flex flex-col items-center justify-center min-h-full px-6 py-16 animate-fade-in">
            {/* Badge */}
            <div className={cn(
              "flex items-center gap-2 px-4 py-2 rounded-full mb-6 border",
              selectedSource === 'frontend'
                ? "bg-blue-50 border-blue-200"
                : "bg-violet-50 border-violet-200"
            )}>
              {selectedSource === 'frontend'
                ? <Monitor className="w-4 h-4 text-blue-500" />
                : <Server className="w-4 h-4 text-violet-500" />
              }
              <span className={cn(
                "text-xs font-bold uppercase tracking-wider",
                selectedSource === 'frontend' ? "text-blue-700" : "text-violet-700"
              )}>
                {selectedSource === 'frontend' ? 'Frontend' : 'Backend'} Documentation
              </span>
            </div>

            <h1 className="text-4xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight text-center mb-3">
              Select Provider
            </h1>
            <p className="text-slate-500 dark:text-slate-400 text-center max-w-lg leading-relaxed mb-12">
              Choose the Git provider where your {selectedSource} repository is hosted.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 max-w-xl w-full">
              {/* GitHub */}
              <button
                onClick={() => handleProviderSelect('github')}
                className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-8 text-center transition-all duration-300 group hover:shadow-card hover:-translate-y-1 relative overflow-hidden"
              >
                <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-slate-700 to-slate-900 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center mx-auto mb-4 group-hover:scale-110 group-hover:shadow-lg transition-all duration-300">
                  <Github className="w-9 h-9 text-slate-900 dark:text-slate-100" />
                </div>
                <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">GitHub</h3>
                <p className="text-xs text-slate-400 dark:text-slate-500 leading-relaxed mb-4">Connect to your GitHub repos</p>
                <div className="flex items-center justify-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  <span className="text-xs font-medium text-emerald-600">Connected</span>
                </div>
              </button>

              {/* GitLab */}
              <button
                onClick={() => handleProviderSelect('gitlab')}
                className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-8 text-center transition-all duration-300 group hover:shadow-card hover:-translate-y-1 relative overflow-hidden"
              >
                <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-orange-400 to-orange-600 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                <div className="w-16 h-16 rounded-2xl bg-orange-50 border border-orange-100 flex items-center justify-center mx-auto mb-4 group-hover:scale-110 group-hover:shadow-lg transition-all duration-300">
                  <GitLabIcon className="w-9 h-9 text-orange-500" />
                </div>
                <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">GitLab</h3>
                <p className="text-xs text-slate-400 dark:text-slate-500 leading-relaxed mb-4">Connect to your GitLab repos</p>
                <div className="flex items-center justify-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  <span className="text-xs font-medium text-emerald-600">Connected</span>
                </div>
              </button>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════
            STEP: Choose Repository
           ══════════════════════════════════════════ */}
        {step === 'choose-repo' && (
          <div className="relative flex flex-col items-center min-h-full px-6 py-12 animate-fade-in">
            {/* Badge */}
            <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full mb-6">
              {selectedProvider === 'github'
                ? <Github className="w-4 h-4 text-slate-700" />
                : <GitLabIcon className="w-4 h-4 text-orange-500" />
              }
              <span className="text-xs font-bold text-slate-700 dark:text-slate-200 uppercase tracking-wider">
                {selectedProvider === 'github' ? 'GitHub' : 'GitLab'} Repositories
              </span>
            </div>

            <h1 className="text-3xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight text-center mb-2">
              Select Repository
            </h1>
            <p className="text-slate-500 dark:text-slate-400 text-center max-w-md leading-relaxed mb-8">
              Choose the repository you want to generate documentation for.
            </p>

            {/* Repository list card */}
            <div className="w-full max-w-2xl bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-soft overflow-hidden">
              {/* Search */}
              <div className="px-5 py-3.5 border-b border-slate-100 dark:border-slate-800 flex items-center gap-3">
                <Search className="w-4 h-4 text-slate-400" />
                <input
                  type="text"
                  placeholder="Search repositories..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="flex-1 bg-transparent border-none outline-none text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500"
                  autoFocus
                />
                <span className="text-[11px] text-slate-400 dark:text-slate-500 font-medium bg-slate-50 dark:bg-slate-800 px-2 py-0.5 rounded-md">{filteredRepos.length} repos</span>
              </div>

              {/* Repo list */}
              <div className="divide-y divide-slate-50 dark:divide-slate-800 max-h-[380px] overflow-y-auto">
                {filteredRepos.map((repo) => (
                  <button
                    key={repo.name}
                    onClick={() => handleRepoSelect(repo)}
                    className="w-full flex items-center gap-4 px-5 py-3.5 text-left hover:bg-blue-50/40 dark:hover:bg-blue-500/5 transition-all duration-200 group"
                  >
                    <div className="w-9 h-9 rounded-lg bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center shrink-0 group-hover:bg-white dark:group-hover:bg-slate-700 group-hover:border-blue-200 dark:group-hover:border-blue-500/30 transition-all">
                      <Code2 className="w-4 h-4 text-slate-400 group-hover:text-blue-500 transition-colors" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors truncate">{repo.name}</h4>
                        {repo.isDemo && (
                          <span className="px-1.5 py-0.5 bg-amber-50 border border-amber-200 rounded text-[9px] font-bold text-amber-600 uppercase">Demo</span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-0.5">
                        <span className="text-[11px] text-slate-400 flex items-center gap-1">
                          <div className="w-2 h-2 rounded-full bg-indigo-400" />
                          {repo.lang}
                        </span>
                        <span className="text-[11px] text-slate-400 flex items-center gap-1">
                          <Star className="w-3 h-3" /> {repo.stars}
                        </span>
                        <span className="text-[11px] text-slate-400 flex items-center gap-1">
                          <Clock className="w-3 h-3" /> {repo.updated}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      <span className="text-xs font-semibold text-blue-600">Generate</span>
                      <ArrowRight className="w-3.5 h-3.5 text-blue-400" />
                    </div>
                  </button>
                ))}
              </div>

              {filteredRepos.length === 0 && (
                <div className="px-6 py-10 text-center">
                  <Search className="w-7 h-7 text-slate-300 mx-auto mb-2" />
                  <p className="text-sm font-medium text-slate-500 dark:text-slate-400">No repositories found</p>
                  <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Try a different search</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════
            STEP: Enter Web URL  (matches image)
           ══════════════════════════════════════════ */}
        {step === 'enter-url' && (
          <div className="relative flex flex-col items-center justify-center min-h-full px-6 py-16 animate-fade-in">
            {/* Badge */}
            <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-cyan-50 to-teal-50 dark:from-cyan-500/10 dark:to-teal-500/10 border border-cyan-200 dark:border-cyan-500/20 rounded-full mb-6">
              <Globe className="w-4 h-4 text-teal-500 dark:text-teal-400" />
              <span className="text-xs font-bold text-teal-700 dark:text-teal-400 uppercase tracking-wider">Website to Documentation</span>
            </div>

            <h1 className="text-4xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight text-center mb-3">
              Generate Documentation
            </h1>
            <p className="text-slate-500 dark:text-slate-400 text-center max-w-lg leading-relaxed mb-10">
              Paste any live website URL and our AI will crawl, analyze, and auto-generate 
              comprehensive technical documentation in seconds.
            </p>

            {/* URL Input */}
            <div className="w-full max-w-xl mb-5">
              <div className="relative">
                <div className="absolute left-5 top-1/2 -translate-y-1/2">
                  <Globe className="w-5 h-5 text-slate-400" />
                </div>
                <input
                  type="url"
                  value={webUrl}
                  onChange={(e) => setWebUrl(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleWebUrlGenerate()}
                  placeholder="https://docs.your-project.com"
                  className="w-full pl-14 pr-5 py-4.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-full text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-cyan-400 dark:focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/10 outline-none transition-all shadow-sm hover:shadow-md"
                  style={{ paddingTop: '18px', paddingBottom: '18px' }}
                  autoFocus
                />
              </div>
            </div>

            {/* Quick Try */}
            <div className="flex items-center gap-2.5 mb-8 flex-wrap justify-center">
              <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest">Quick Try:</span>
              {['docs.github.com', 'nextjs.org/docs', 'tailwindcss.com'].map((url) => (
                <button
                  key={url}
                  onClick={() => setWebUrl(`https://${url}`)}
                  className="px-3 py-1.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-xs text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700 hover:border-slate-300 dark:hover:border-slate-600 hover:text-slate-700 dark:hover:text-slate-200 transition-all shadow-sm"
                >
                  {url}
                </button>
              ))}
            </div>

            {/* Start AI Analysis Button */}
            <button
              onClick={handleWebUrlGenerate}
              disabled={!webUrl.trim()}
              className={cn(
                "flex items-center justify-center gap-2.5 px-10 py-4 rounded-full text-sm font-bold transition-all duration-300 mb-5",
                webUrl.trim()
                  ? "bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-lg shadow-blue-500/25 hover:shadow-xl hover:shadow-blue-500/30 hover:-translate-y-0.5 active:scale-[0.98]"
                  : "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-not-allowed"
              )}
            >
              <Sparkles className="w-5 h-5" />
              Start AI Analysis
            </button>

            {/* Helper text */}
            <div className="flex items-center gap-1.5 text-slate-400">
              <Info className="w-3.5 h-3.5" />
              <span className="text-xs">AI will detect frameworks, API structures, and components automatically.</span>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════
            STEP: Generating
           ══════════════════════════════════════════ */}
        {step === 'generating' && (
          <div className="relative flex flex-col items-center justify-center min-h-full px-6 py-16 animate-fade-in">
            {/* Animated Spinner */}
            <div className="relative w-28 h-28 mx-auto mb-8">
              <div className="absolute inset-0 rounded-full border-4 border-slate-100 dark:border-slate-800" />
              <div
                className="absolute inset-0 rounded-full border-4 border-transparent border-t-cyan-500 border-r-blue-500 animate-spin"
                style={{ animationDuration: '1.2s' }}
              />
              <div className="absolute inset-3 rounded-full bg-white dark:bg-slate-900 shadow-soft flex items-center justify-center">
                {generationProgress >= 100 ? (
                  <CheckCircle2 className="w-9 h-9 text-teal-500 animate-fade-in" />
                ) : (
                  <Sparkles className="w-8 h-8 text-cyan-500" />
                )}
              </div>
            </div>

            <h2 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 mb-2 text-center">
              {generationProgress >= 100 ? 'Documentation Ready!' : 'Analyzing & Generating'}
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-8 text-center">{generationStep}</p>

            {/* Progress bar */}
            <div className="w-full max-w-md">
              <div className="w-full bg-slate-100 dark:bg-slate-800 rounded-full h-2 overflow-hidden mb-2">
                <div
                  className="h-full bg-gradient-to-r from-cyan-500 to-blue-600 rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${generationProgress}%` }}
                />
              </div>
              <p className="text-xs font-semibold text-slate-400 dark:text-slate-500 text-center">{generationProgress}% complete</p>
            </div>
          </div>
        )}
      </div>

  
    </div>
  );
}
