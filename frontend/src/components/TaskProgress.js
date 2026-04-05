import React, { useState } from 'react';
import { cn } from '@/lib/utils';

/**
 * TaskProgress — Compact horizontal pipeline indicator for inline chat.
 * Shows phase dots with current step label. Expandable on click.
 *
 * @param {Array} phases - Array of { phase, title, description, status }
 * @param {string} status - Overall task status
 * @param {string} completionSummary - Summary text when all phases are done
 */
export default function TaskProgress({ phases = [], status, completionSummary = '' }) {
  const [expanded, setExpanded] = useState(false);

  if (phases.length === 0) return null;

  const totalPhases = Math.max(phases.length, 4);
  const completedPhases = phases.filter(p => p.status === 'done').length;
  const hasError = phases.some(p => p.status === 'error');
  const allDone = completedPhases >= totalPhases;
  const activePhase = phases.find(p => p.status === 'active');

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  //  COMPLETION CARD — compact success banner
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  if (allDone && status === 'ready') {
    return (
      <div className="w-full my-4 animate-in fade-in zoom-in-95 duration-500">
        <div className="bg-gradient-to-r from-emerald-50 to-green-50 dark:from-emerald-950/20 dark:to-green-950/10 border border-emerald-200 dark:border-emerald-800/50 rounded-2xl overflow-hidden">
          {/* Compact success header */}
          <div className="px-5 py-3 flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-emerald-500 flex items-center justify-center shrink-0 shadow-sm shadow-emerald-500/20">
              <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-emerald-800 dark:text-emerald-200">Task Complete</h2>
                <span className="px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-500/15 border border-emerald-200 dark:border-emerald-500/25 text-[9px] font-bold uppercase tracking-widest text-emerald-600 dark:text-emerald-400">
                  {completedPhases} phases
                </span>
              </div>

              {/* Compact phase dots */}
              <div className="flex items-center gap-1.5 mt-2">
                {phases.map((phase) => (
                  <div
                    key={phase.phase}
                    className="group relative"
                  >
                    <div className={cn(
                      "w-6 h-1.5 rounded-full transition-all",
                      phase.status === 'done' ? "bg-emerald-400" :
                      phase.status === 'error' ? "bg-red-400" :
                      "bg-slate-200 dark:bg-white/10"
                    )} />
                    {/* Tooltip */}
                    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-slate-800 text-white text-[9px] rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                      {phase.title}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Expand toggle */}
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 hover:text-emerald-800 dark:hover:text-emerald-200 px-2 py-1 rounded-lg hover:bg-emerald-100 dark:hover:bg-emerald-500/10 transition-colors"
            >
              {expanded ? 'Less' : 'Details'}
            </button>
          </div>

          {/* Expandable details */}
          {expanded && (
            <div className="px-5 pb-4 pt-0 space-y-1.5 animate-in slide-in-from-top-1 fade-in duration-200">
              <div className="h-px bg-emerald-200 dark:bg-emerald-500/15 mb-3" />
              {phases.map((phase) => (
                <div key={phase.phase} className="flex items-center gap-2.5">
                  <div className={cn(
                    "w-4 h-4 rounded-full flex items-center justify-center shrink-0",
                    phase.status === 'done' ? "bg-emerald-500 text-white" :
                    phase.status === 'error' ? "bg-red-500 text-white" :
                    "bg-slate-200 dark:bg-slate-700"
                  )}>
                    {phase.status === 'done' && (
                      <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                    {phase.status === 'error' && (
                      <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    )}
                  </div>
                  <span className="text-xs font-medium text-slate-600 dark:text-slate-300">{phase.title}</span>
                  {phase.description && (
                    <span className="text-[10px] text-slate-400 dark:text-slate-500 truncate max-w-[180px]">{phase.description}</span>
                  )}
                </div>
              ))}

              {completionSummary && (
                <div className="mt-3 pt-3 border-t border-emerald-100 dark:border-emerald-900/30">
                  <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{completionSummary}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  //  IN-PROGRESS — compact horizontal indicator
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  return (
    <div className="w-full my-4 animate-in fade-in duration-500">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm overflow-hidden">
        {/* Compact header */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full px-5 py-3 flex items-center gap-3 hover:bg-slate-50 dark:hover:bg-white/[0.02] transition-colors"
        >
          {/* Spinner */}
          <div className={cn(
            "w-8 h-8 rounded-xl flex items-center justify-center shrink-0",
            hasError ? "bg-red-50 dark:bg-red-500/10" : "bg-blue-50 dark:bg-blue-500/10"
          )}>
            {hasError ? (
              <svg className="w-4 h-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            ) : (
              <div className="w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
            )}
          </div>

          {/* Label */}
          <div className="flex-1 min-w-0 text-left">
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-slate-900 dark:text-slate-100">
                {activePhase?.title || 'Processing...'}
              </span>
              <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500">
                {completedPhases}/{totalPhases}
              </span>
            </div>

            {/* Progress bar dots */}
            <div className="flex items-center gap-1 mt-1.5">
              {phases.map((phase) => (
                <div
                  key={phase.phase}
                  className={cn(
                    "h-1.5 rounded-full flex-1 transition-all duration-500",
                    phase.status === 'done' ? "bg-emerald-400" :
                    phase.status === 'active' ? "bg-blue-500 animate-pulse" :
                    phase.status === 'error' ? "bg-red-400" :
                    "bg-slate-200 dark:bg-white/10"
                  )}
                />
              ))}
            </div>
          </div>

          {/* Expand arrow */}
          <svg
            className={cn("w-4 h-4 text-slate-400 transition-transform shrink-0", expanded && "rotate-180")}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {/* Expanded timeline */}
        {expanded && (
          <div className="px-5 pb-4 space-y-0 animate-in slide-in-from-top-1 fade-in duration-200 border-t border-slate-100 dark:border-white/[0.05] pt-3">
            {phases.map((phase, index) => {
              const isDone = phase.status === 'done';
              const isActive = phase.status === 'active';
              const isError = phase.status === 'error';
              const isLast = index === phases.length - 1;

              return (
                <div key={phase.phase} className="flex gap-3">
                  {/* Timeline */}
                  <div className="flex flex-col items-center shrink-0 w-5">
                    {isDone ? (
                      <div className="w-5 h-5 rounded-full bg-emerald-500 text-white flex items-center justify-center shadow-sm z-10">
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    ) : isActive ? (
                      <div className="w-5 h-5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin z-10 bg-white dark:bg-slate-900" />
                    ) : isError ? (
                      <div className="w-5 h-5 rounded-full bg-red-500 text-white flex items-center justify-center shadow-sm z-10">
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </div>
                    ) : (
                      <div className="w-3 h-3 mt-1 rounded-full bg-slate-200 dark:bg-slate-700 z-10" />
                    )}
                    {!isLast && (
                      <div className={cn(
                        "w-[2px] flex-1 min-h-[20px] -my-0.5",
                        isDone ? "bg-emerald-400" : "bg-slate-100 dark:bg-slate-800"
                      )} />
                    )}
                  </div>
                  {/* Content */}
                  <div className="flex-1 pb-4 pt-0">
                    <h3 className={cn(
                      "font-bold text-xs transition-colors",
                      isDone || isActive ? "text-slate-900 dark:text-slate-100" :
                      isError ? "text-red-600 dark:text-red-400" :
                      "text-slate-400 dark:text-slate-500"
                    )}>
                      {phase.title}
                    </h3>
                    {phase.description && (
                      <p className={cn(
                        "text-[11px] mt-0.5 leading-snug",
                        isError ? "text-red-500 dark:text-red-400 italic" : "text-slate-500 dark:text-slate-400 italic"
                      )}>
                        {phase.description}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
