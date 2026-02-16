'use client';

import { 
  GitBranch, ArrowLeft, ArrowRight, Shield, 
  AlertTriangle, ChevronDown, Check, Sparkles, Loader2,
  Github, Lock, Unlock, Zap
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useEffect } from 'react';
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

export default function SessionPage() {
  const router = useRouter();
  const {
    selectedRepo,
    sourceBranch, setSourceBranch,
    createFeatureBranch, setCreateFeatureBranch,
    targetBranch, setTargetBranch,
    setSessionActive,
    activeProvider,
  } = useFlowStore();

  const [isStarting, setIsStarting] = useState(false);
  const [showSourceDropdown, setShowSourceDropdown] = useState(false);

  const branches = ['main', 'develop', 'staging', 'release/v2.0'];

  // If no repo selected, redirect back
  useEffect(() => {
    if (!selectedRepo) {
      router.push('/projects');
    }
  }, [selectedRepo, router]);

  const repoName = selectedRepo?.name || 'unknown-repo';
  const workingBranch = createFeatureBranch ? targetBranch : sourceBranch;
  const isDirectToProduction = !createFeatureBranch && sourceBranch === 'main';

  const handleStartSession = () => {
    setIsStarting(true);
    setSessionActive(true);
    setTimeout(() => {
      router.push(`/workspace/${encodeURIComponent(repoName)}`);
    }, 1500);
  };

  if (!selectedRepo) return null;

  return (
    <div className="min-h-screen bg-[#fafbfc] relative overflow-hidden">
      
      {/* Background */}
      <div className="absolute inset-0 bg-dot-grid opacity-40" />
      <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-blue-500/30 to-transparent" />

      {/* ── Top Bar ── */}
      <header className="relative z-20 h-16 border-b border-slate-100 flex items-center justify-between px-8 bg-white/90 backdrop-blur-sm">
        <div className="flex items-center gap-6">
          <button onClick={() => router.push('/projects')} className="flex items-center gap-2 text-slate-400 hover:text-slate-600 transition-colors group">
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
            <span className="text-sm font-medium">Back to Projects</span>
          </button>
          <div className="h-5 w-px bg-slate-200" />
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shadow-sm">
              <Zap className="w-4 h-4 text-white fill-current" />
            </div>
            <span className="font-bold text-slate-900 text-sm tracking-tight">Lucid AI</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500 font-mono">{repoName}</span>
          {activeProvider === 'github' ? (
            <Github className="w-4 h-4 text-slate-400" />
          ) : activeProvider === 'gitlab' ? (
            <GitLabIcon className="w-4 h-4 text-orange-500" />
          ) : (
            <span className="text-[9px] font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">DEMO</span>
          )}
        </div>
      </header>

      {/* ── Main Content ── */}
      <main className="relative z-10 max-w-2xl mx-auto px-8 py-16">
        
        {/* Title */}
        <div className="mb-12 animate-slide-up">
          <div className="inline-flex items-center gap-2 bg-emerald-50 border border-emerald-200 px-3 py-1.5 rounded-full mb-4">
            <Shield className="w-3.5 h-3.5 text-emerald-600" />
            <span className="text-[11px] font-semibold text-emerald-600 uppercase tracking-wider">Safe Mode</span>
          </div>
          <h1 className="text-3xl font-extrabold text-slate-900 tracking-tight mb-2">Configure Session</h1>
          <p className="text-slate-500 font-medium">Set up your branching strategy before the AI starts working.</p>
        </div>

        {/* ── Session Config Card ── */}
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-soft animate-slide-up" style={{ animationDelay: '0.1s' }}>

          {/* Repo Header */}
          <div className="px-8 py-5 border-b border-slate-100 flex items-center gap-4 bg-slate-50/50">
            <div className="w-10 h-10 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center">
              <GitBranch className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900">{repoName}</h3>
              <p className="text-[11px] text-slate-400">{selectedRepo?.lang} · Last updated {selectedRepo?.updated}</p>
            </div>
          </div>

          {/* Config Body */}
          <div className="px-8 py-8 space-y-8">
            
            {/* Source Branch */}
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-3">
                Source Branch
              </label>
              <div className="relative">
                <button
                  onClick={() => setShowSourceDropdown(!showSourceDropdown)}
                  className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm font-medium text-slate-700 hover:border-blue-300 transition-all"
                >
                  <div className="flex items-center gap-2.5">
                    <GitBranch className="w-4 h-4 text-blue-600" />
                    <span>{sourceBranch}</span>
                    {sourceBranch === 'main' && (
                      <span className="text-[9px] font-bold text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200">DEFAULT</span>
                    )}
                  </div>
                  <ChevronDown className={cn("w-4 h-4 text-slate-400 transition-transform", showSourceDropdown && "rotate-180")} />
                </button>

                {/* Dropdown */}
                {showSourceDropdown && (
                  <div className="absolute top-full left-0 right-0 mt-2 bg-white rounded-xl border border-slate-200 overflow-hidden z-30 shadow-lg animate-slide-down">
                    {branches.map((branch) => (
                      <button
                        key={branch}
                        onClick={() => { setSourceBranch(branch); setShowSourceDropdown(false); }}
                        className={cn(
                          "w-full flex items-center gap-3 px-4 py-3 text-sm transition-all text-left",
                          sourceBranch === branch
                            ? "bg-blue-50 text-blue-700"
                            : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                        )}
                      >
                        <GitBranch className="w-3.5 h-3.5" />
                        <span className="font-medium">{branch}</span>
                        {sourceBranch === branch && <Check className="w-3.5 h-3.5 ml-auto text-blue-600" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <p className="text-[11px] text-slate-400 mt-2">The base branch the AI will work from.</p>
            </div>

            {/* Divider */}
            <div className="border-t border-slate-100" />

            {/* Target Branch (Safety Feature) */}
            <div>
              <div className="flex items-center justify-between mb-4">
                <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                  Target Branch
                </label>

                {/* Toggle */}
                <button
                  onClick={() => setCreateFeatureBranch(!createFeatureBranch)}
                  className="flex items-center gap-2.5 group"
                >
                  <span className="text-xs font-medium text-slate-400 group-hover:text-slate-600 transition-colors">
                    Create new feature branch
                  </span>
                  <div className={cn(
                    "w-10 h-[22px] rounded-full p-0.5 transition-colors duration-300 cursor-pointer",
                    createFeatureBranch ? "bg-blue-600" : "bg-slate-300"
                  )}>
                    <div className={cn(
                      "w-[18px] h-[18px] bg-white rounded-full shadow-sm transition-transform duration-300",
                      createFeatureBranch ? "translate-x-[18px]" : "translate-x-0"
                    )} />
                  </div>
                </button>
              </div>

              {/* Feature branch input */}
              <div className={cn(
                "overflow-hidden transition-all duration-500 ease-in-out",
                createFeatureBranch ? "max-h-32 opacity-100" : "max-h-0 opacity-0"
              )}>
                <div className="relative">
                  <GitBranch className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-emerald-500" />
                  <input
                    value={targetBranch}
                    onChange={(e) => setTargetBranch(e.target.value)}
                    className="w-full pl-11 pr-4 py-3 bg-emerald-50 border border-emerald-200 rounded-xl text-sm font-mono text-emerald-700 placeholder:text-emerald-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
                    placeholder="ai-feat-0215"
                  />
                </div>
                <div className="flex items-center gap-2 mt-2.5">
                  <Lock className="w-3 h-3 text-emerald-600" />
                  <p className="text-[11px] text-emerald-600 font-medium">
                    Changes will be isolated in a feature branch. Safe for production.
                  </p>
                </div>
              </div>

              {/* ── DANGER WARNING: Direct to production ── */}
              {isDirectToProduction && (
                <div className="mt-3 px-4 py-3 bg-amber-50 border border-amber-200 rounded-xl flex items-start gap-3 animate-fade-in animate-warning-pulse">
                  <AlertTriangle className="w-4.5 h-4.5 text-amber-500 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-xs font-bold text-amber-700 mb-0.5">Direct Production Commit</p>
                    <p className="text-[11px] text-amber-600 leading-relaxed">
                      AI will commit directly to <span className="font-mono font-bold text-amber-700">main</span>. This bypasses all safety branching.
                    </p>
                  </div>
                </div>
              )}

              {!createFeatureBranch && !isDirectToProduction && (
                <div className="mt-3 flex items-center gap-2">
                  <Unlock className="w-3 h-3 text-slate-400" />
                  <p className="text-[11px] text-slate-400 font-medium">
                    AI will commit to <span className="font-mono font-bold text-slate-600">{sourceBranch}</span> directly.
                  </p>
                </div>
              )}
            </div>

            {/* Divider */}
            <div className="border-t border-slate-100" />

            {/* Summary */}
            <div className="bg-slate-50 rounded-xl p-5 border border-slate-100">
              <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-4">Session Summary</h4>
              <div className="space-y-3">
                <SummaryRow label="Repository" value={repoName} />
                <SummaryRow label="Base Branch" value={sourceBranch} />
                <SummaryRow 
                  label="Working Branch" 
                  value={workingBranch}
                  highlight={createFeatureBranch}
                  warning={isDirectToProduction}
                />
                <SummaryRow label="Mode" value={createFeatureBranch ? '🛡 Safe Mode' : isDirectToProduction ? '⚠️ Production Mode' : '📝 Direct Mode'} />
              </div>
            </div>
          </div>

          {/* Action Button */}
          <div className="px-8 py-6 border-t border-slate-100 bg-slate-50/30">
            <button
              onClick={handleStartSession}
              disabled={isStarting}
              className={cn(
                "w-full py-4 rounded-xl font-bold text-base flex items-center justify-center gap-3 transition-all duration-500 relative overflow-hidden group",
                isStarting
                  ? "bg-slate-100 text-slate-400 cursor-wait"
                  : "bg-blue-600 text-white hover:bg-blue-700 shadow-lg shadow-blue-600/20 hover:-translate-y-0.5"
              )}
            >
              {isStarting ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  <span>Initializing session...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-5 h-5" />
                  <span>Start Session</span>
                </>
              )}
            </button>
          </div>
        </div>

      </main>
    </div>
  );
}

function SummaryRow({ label, value, highlight, warning }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-slate-400 font-medium">{label}</span>
      <span className={cn(
        "text-xs font-mono font-bold",
        warning ? "text-amber-600" : highlight ? "text-emerald-600" : "text-slate-700"
      )}>
        {value}
      </span>
    </div>
  );
}
