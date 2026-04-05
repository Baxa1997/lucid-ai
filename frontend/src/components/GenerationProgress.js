'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — GenerationProgress (Compact Inline Version)
//  Minimal inline progress indicator for the chat stream.
//  The full progress visualization is in BuildProgressPanel.
// ─────────────────────────────────────────────────────────

import { useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { Sparkles, Check, Loader2 } from 'lucide-react';

const BATCH_ORDER = [
  'Foundation',
  'UI Components',
  'Layout',
  'Pages',
  'Integration',
];

export default function GenerationProgress({ isVisible = false }) {
  const [percentage, setPercentage] = useState(0);
  const [currentBatch, setCurrentBatch] = useState('');
  const [currentMessage, setCurrentMessage] = useState('Preparing workspace...');
  const [batchStatuses, setBatchStatuses] = useState({});
  const [allFilesCount, setAllFilesCount] = useState(0);
  const [isComplete, setIsComplete] = useState(false);

  // Listen for WebSocket messages
  useEffect(() => {
    if (!isVisible) return;

    const handleProgress = (e) => {
      const { batch, message, files, percentage: pct } = e.detail;
      setPercentage(pct || 0);
      setCurrentBatch(batch || '');
      setCurrentMessage(message || '');
      if (batch) {
        setBatchStatuses(prev => ({ ...prev, [batch]: 'active' }));
      }
      if (files?.length) {
        setAllFilesCount(prev => prev + files.length);
      }
    };

    const handleBatchComplete = (e) => {
      const { batch } = e.detail;
      if (batch) {
        setBatchStatuses(prev => ({ ...prev, [batch]: 'done' }));
      }
    };

    const handleComplete = (e) => {
      const { total_files } = e.detail;
      setIsComplete(true);
      setPercentage(100);
      if (total_files) setAllFilesCount(total_files);
      BATCH_ORDER.forEach(name => {
        setBatchStatuses(prev => ({ ...prev, [name]: 'done' }));
      });
    };

    window.addEventListener('generation_progress', handleProgress);
    window.addEventListener('batch_complete', handleBatchComplete);
    window.addEventListener('generation_complete', handleComplete);

    return () => {
      window.removeEventListener('generation_progress', handleProgress);
      window.removeEventListener('batch_complete', handleBatchComplete);
      window.removeEventListener('generation_complete', handleComplete);
    };
  }, [isVisible]);

  if (!isVisible) return null;

  // Compact inline indicator
  return (
    <div className={cn(
      "w-full my-3 animate-in fade-in duration-500",
    )}>
      <div className={cn(
        "flex items-center gap-3 px-4 py-3 rounded-xl border transition-all",
        isComplete
          ? "bg-emerald-50 dark:bg-emerald-500/5 border-emerald-200 dark:border-emerald-500/20"
          : "bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800"
      )}>
        {/* Icon */}
        {isComplete ? (
          <div className="w-7 h-7 rounded-lg bg-emerald-100 dark:bg-emerald-500/15 flex items-center justify-center shrink-0">
            <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
          </div>
        ) : (
          <div className="w-7 h-7 rounded-lg bg-blue-50 dark:bg-blue-500/10 flex items-center justify-center shrink-0">
            <Sparkles className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400 animate-pulse" />
          </div>
        )}

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn(
              "text-xs font-bold",
              isComplete ? "text-emerald-700 dark:text-emerald-300" : "text-slate-800 dark:text-white"
            )}>
              {isComplete ? 'Project Generated!' : currentBatch || 'Generating...'}
            </span>
            <span className={cn(
              "text-[10px] font-bold",
              isComplete ? "text-emerald-600 dark:text-emerald-400" : "text-blue-600 dark:text-blue-400"
            )}>
              {Math.round(percentage)}%
            </span>
          </div>

          {/* Mini progress bar */}
          <div className="flex items-center gap-1 mt-1.5">
            {BATCH_ORDER.map(name => {
              const st = batchStatuses[name];
              return (
                <div
                  key={name}
                  className={cn(
                    "h-1 rounded-full flex-1 transition-all duration-500",
                    st === 'done' ? "bg-emerald-400" :
                    st === 'active' ? "bg-blue-500 animate-pulse" :
                    "bg-slate-200 dark:bg-white/10"
                  )}
                />
              );
            })}
          </div>
        </div>

        {/* File count */}
        <span className="text-[10px] font-mono font-medium text-slate-400 dark:text-white/30 shrink-0">
          {allFilesCount} files
        </span>
      </div>
    </div>
  );
}
