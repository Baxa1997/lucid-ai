import React from 'react';

export default function TaskProgress({ phases = [], status, completionSummary = '' }) {
  // Hide if no phases
  if (phases.length === 0) return null;

  const totalPhases = Math.max(phases.length, 4);
  const completedPhases = phases.filter(p => p.status === 'done').length;
  const hasError = phases.some(p => p.status === 'error');
  const allDone = completedPhases >= totalPhases;

  let displayStatus = "PLANNING";
  if (phases.some(p => p.phase >= 3 && (p.status === 'active' || p.status === 'done'))) displayStatus = "WORKING";
  if (allDone) displayStatus = "COMPLETE";
  if (hasError) displayStatus = "ERROR";

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  //  COMPLETION CARD — beautiful, polished "task done" UI
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  if (allDone && status === 'ready') {
    return (
      <div className="w-full my-6 animate-in fade-in zoom-in-95 duration-500">
        <div className="bg-gradient-to-br from-emerald-50 via-white to-emerald-50/30 dark:from-emerald-950/20 dark:via-slate-900 dark:to-emerald-950/10 border border-emerald-200 dark:border-emerald-800/50 rounded-2xl shadow-sm overflow-hidden">
          
          {/* Success banner */}
          <div className="bg-emerald-500 dark:bg-emerald-600 px-6 py-4 flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-white/20 backdrop-blur-sm flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-base font-bold text-white">Task Completed</h2>
              <p className="text-emerald-100 text-xs mt-0.5">
                All {completedPhases} phases finished successfully
              </p>
            </div>
            <div className="px-3 py-1 bg-white/15 backdrop-blur-sm rounded-full border border-white/20">
              <span className="text-[10px] font-bold tracking-widest uppercase text-white">
                DONE
              </span>
            </div>
          </div>

          {/* Phase summary */}
          <div className="px-6 py-5">
            <div className="space-y-2.5">
              {phases.map((phase) => {
                const isDone = phase.status === 'done';
                const isError = phase.status === 'error';

                return (
                  <div
                    key={phase.phase}
                    className="flex items-center gap-3 group"
                  >
                    {/* Status icon */}
                    {isDone ? (
                      <div className="w-5 h-5 rounded-full bg-emerald-500 text-white flex items-center justify-center shrink-0 shadow-sm">
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    ) : isError ? (
                      <div className="w-5 h-5 rounded-full bg-red-500 text-white flex items-center justify-center shrink-0 shadow-sm">
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </div>
                    ) : (
                      <div className="w-5 h-5 rounded-full bg-slate-200 dark:bg-slate-700 shrink-0" />
                    )}
                    
                    {/* Phase info */}
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
                        {phase.title}
                      </span>
                    </div>
                    
                    {/* Description as subtitle */}
                    {phase.description && (
                      <span className="text-xs text-slate-400 dark:text-slate-500 truncate max-w-[200px]">
                        {phase.description}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Completion summary */}
            {completionSummary && (
              <div className="mt-5 pt-4 border-t border-emerald-100 dark:border-emerald-900/30">
                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
                  {completionSummary}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  //  IN-PROGRESS CARD — standard pipeline progress UI
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  return (
    <div className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm p-6 my-6 animate-in fade-in duration-500">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
            hasError
              ? 'bg-red-100 dark:bg-red-500/15'
              : 'bg-blue-100 dark:bg-blue-500/15'
          }`}>
            {hasError ? (
              <svg className="w-4 h-4 text-red-600 dark:text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            ) : (
              <div className="w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
            )}
          </div>
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-slate-100">Task Progress</h2>
            <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">
              {completedPhases}/{totalPhases} phases
            </p>
          </div>
        </div>
        
        <div className={`px-3 py-1 text-[10px] font-bold tracking-widest uppercase rounded-full border ${
          displayStatus === 'ERROR'
            ? 'bg-red-50 text-red-600 border-red-200 dark:bg-red-950/30 dark:text-red-400 dark:border-red-800'
          : displayStatus === 'WORKING'
            ? 'bg-blue-50 text-blue-600 border-blue-200 dark:bg-blue-950/30 dark:text-blue-400 dark:border-blue-800'
            : 'bg-amber-50 text-amber-600 border-amber-200 dark:bg-amber-950/30 dark:text-amber-400 dark:border-amber-800'
        }`}>
          {displayStatus}
        </div>
      </div>

      {/* Phase List */}
      <div className="space-y-0 relative">
        {phases.map((phase, index) => {
          const isDone = phase.status === 'done';
          const isActive = phase.status === 'active';
          const isError = phase.status === 'error';
          const isLast = index === phases.length - 1;

          return (
            <div 
              key={phase.phase} 
              className="flex gap-4 transition-all duration-500"
            >
              {/* Timeline indicator */}
              <div className="flex flex-col items-center shrink-0 w-6">
                {isDone ? (
                  <div className="w-5 h-5 rounded-full bg-emerald-500 text-white flex items-center justify-center shadow-sm z-10 transition-all duration-500">
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
                
                {/* Connecting line */}
                {!isLast && (
                  <div className={`w-[2px] flex-1 min-h-[24px] -my-0.5 ${
                    isDone ? 'bg-emerald-400 dark:bg-emerald-500/50' : 'bg-slate-100 dark:bg-slate-800'
                  }`} />
                )}
              </div>

              {/* Content */}
              <div className="flex-1 pb-5 mt-0">
                <h3 className={`font-bold text-sm transition-colors duration-300 ${
                  isDone ? 'text-slate-900 dark:text-slate-100' :
                  isActive ? 'text-slate-900 dark:text-slate-100' :
                  isError ? 'text-red-600 dark:text-red-400' :
                  'text-slate-400 dark:text-slate-500'
                }`}>
                  {phase.title}
                </h3>
                
                {phase.description && (
                  <p className={`text-sm mt-1.5 leading-relaxed animate-in slide-in-from-top-1 fade-in duration-500 ${
                    isError 
                      ? 'text-red-500 dark:text-red-400 italic'
                      : 'text-slate-500 dark:text-slate-400 italic'
                  }`}>
                    {phase.description}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
