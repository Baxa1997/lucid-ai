import React from 'react';

export default function TaskProgress({ phases = [], status }) {
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

  return (
    <div className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm p-6 my-6 animate-in fade-in duration-500">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
            allDone
              ? 'bg-emerald-100 dark:bg-emerald-500/15'
              : 'bg-blue-100 dark:bg-blue-500/15'
          }`}>
            {allDone ? (
              <svg className="w-4.5 h-4.5 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
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
          displayStatus === 'COMPLETE' 
            ? 'bg-emerald-50 text-emerald-600 border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-400 dark:border-emerald-800'
          : displayStatus === 'ERROR'
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
