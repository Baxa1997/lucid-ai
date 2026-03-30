'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — GenerationProgress
//  Real-time progress display during new project generation.
//  
//  Handles WebSocket messages:
//    - generation_progress: batch-level progress
//    - batch_complete: individual batch done
//    - generation_complete: entire project done
// ─────────────────────────────────────────────────────────

import { useState, useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import {
  Check, Loader2, FileCode2, Layers, Sparkles,
  Clock, ChevronDown, ChevronRight, Package,
} from 'lucide-react';

const BATCH_ORDER = [
  'Foundation',
  'UI Components',
  'Layout',
  'Pages',
  'Integration',
];

function getBatchIcon(batch) {
  if (/foundation|config|util/i.test(batch)) return Package;
  if (/component|ui/i.test(batch)) return Layers;
  if (/layout|shell/i.test(batch)) return Layers;
  if (/page|route/i.test(batch)) return FileCode2;
  return FileCode2;
}

export default function GenerationProgress({ isVisible = false }) {
  const [percentage, setPercentage] = useState(0);
  const [currentBatch, setCurrentBatch] = useState('');
  const [currentMessage, setCurrentMessage] = useState('Preparing workspace...');
  const [batches, setBatches] = useState([]); // { name, status: 'pending'|'active'|'done', files: [] }
  const [allFiles, setAllFiles] = useState([]);
  const [isComplete, setIsComplete] = useState(false);
  const [totalFiles, setTotalFiles] = useState(0);
  const [startTime] = useState(Date.now());
  const [showFiles, setShowFiles] = useState(false);
  const filesEndRef = useRef(null);

  // Initialize batches
  useEffect(() => {
    if (isVisible && batches.length === 0) {
      setBatches(BATCH_ORDER.map(name => ({
        name,
        status: 'pending',
        files: [],
        filesExpected: 0,
      })));
    }
  }, [isVisible]);

  // Auto-scroll files list
  useEffect(() => {
    if (showFiles && filesEndRef.current) {
      filesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [allFiles, showFiles]);

  // Listen for WebSocket messages (dispatched via custom events)
  useEffect(() => {
    if (!isVisible) return;

    const handleProgress = (e) => {
      const { batch, message, files, percentage: pct } = e.detail;
      
      setPercentage(pct || 0);
      setCurrentBatch(batch || '');
      setCurrentMessage(message || '');

      // Update batch status
      setBatches(prev => prev.map(b => {
        if (b.name === batch) {
          return { ...b, status: 'active', files: [...b.files, ...(files || [])] };
        }
        return b;
      }));

      // Add to global file list
      if (files?.length) {
        setAllFiles(prev => [...prev, ...files.filter(f => !prev.includes(f))]);
      }
    };

    const handleBatchComplete = (e) => {
      const { batch, files_created, files_expected } = e.detail;

      setBatches(prev => prev.map(b => {
        if (b.name === batch) {
          return {
            ...b,
            status: 'done',
            files: files_created || b.files,
            filesExpected: files_expected || b.filesExpected,
          };
        }
        return b;
      }));

      if (files_created?.length) {
        setAllFiles(prev => [...prev, ...files_created.filter(f => !prev.includes(f))]);
      }
    };

    const handleComplete = (e) => {
      const { total_files, message } = e.detail;
      setIsComplete(true);
      setPercentage(100);
      setTotalFiles(total_files || allFiles.length);
      setCurrentMessage(message || '✅ Project generated!');

      // Mark all batches as done
      setBatches(prev => prev.map(b => ({ ...b, status: 'done' })));
    };

    window.addEventListener('generation_progress', handleProgress);
    window.addEventListener('batch_complete', handleBatchComplete);
    window.addEventListener('generation_complete', handleComplete);

    return () => {
      window.removeEventListener('generation_progress', handleProgress);
      window.removeEventListener('batch_complete', handleBatchComplete);
      window.removeEventListener('generation_complete', handleComplete);
    };
  }, [isVisible, allFiles]);

  // Estimate remaining time
  const elapsed = (Date.now() - startTime) / 1000;
  const estimatedTotal = percentage > 5 ? (elapsed / percentage) * 100 : 0;
  const remaining = Math.max(0, estimatedTotal - elapsed);
  const remainingMin = Math.floor(remaining / 60);
  const remainingSec = Math.floor(remaining % 60);

  if (!isVisible) return null;

  return (
    <div className={cn(
      'w-full rounded-2xl border overflow-hidden transition-all duration-500',
      isComplete
        ? 'bg-emerald-50/50 dark:bg-emerald-500/5 border-emerald-200 dark:border-emerald-500/20'
        : 'bg-white dark:bg-[#111620] border-slate-200 dark:border-white/[0.07]'
    )}>
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-100 dark:border-white/[0.05]">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2.5">
            {isComplete ? (
              <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-500/15 flex items-center justify-center">
                <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
              </div>
            ) : (
              <div className="w-8 h-8 rounded-lg bg-blue-50 dark:bg-blue-500/10 flex items-center justify-center">
                <Sparkles className="w-4 h-4 text-blue-600 dark:text-blue-400 animate-pulse" />
              </div>
            )}
            <div>
              <h4 className="text-sm font-bold text-slate-800 dark:text-white">
                {isComplete ? 'Generation Complete!' : 'Generating Your Project...'}
              </h4>
              <p className="text-[11px] text-slate-400 dark:text-white/35 mt-0.5">
                {currentMessage}
              </p>
            </div>
          </div>

          {/* Time estimate */}
          {!isComplete && percentage > 5 && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-50 dark:bg-white/[0.04] rounded-lg border border-slate-100 dark:border-white/[0.06]">
              <Clock className="w-3 h-3 text-slate-400 dark:text-white/30" />
              <span className="text-[11px] font-mono font-medium text-slate-500 dark:text-white/40">
                ~{remainingMin > 0 ? `${remainingMin}m ` : ''}{remainingSec}s
              </span>
            </div>
          )}
        </div>

        {/* Progress bar */}
        <div className="relative h-2 bg-slate-100 dark:bg-white/[0.06] rounded-full overflow-hidden">
          <div
            className={cn(
              'absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out',
              isComplete
                ? 'bg-emerald-500'
                : 'bg-gradient-to-r from-blue-500 to-indigo-500'
            )}
            style={{ width: `${percentage}%` }}
          />
          {!isComplete && (
            <div
              className="absolute inset-y-0 rounded-full bg-white/20 animate-pulse"
              style={{ left: `${Math.max(0, percentage - 8)}%`, width: '8%' }}
            />
          )}
        </div>

        <div className="flex items-center justify-between mt-1.5">
          <span className="text-[10px] font-bold text-slate-400 dark:text-white/30">
            {Math.round(percentage)}%
          </span>
          <span className="text-[10px] text-slate-400 dark:text-white/30">
            {allFiles.length} file{allFiles.length !== 1 ? 's' : ''} created
          </span>
        </div>
      </div>

      {/* Batch list */}
      <div className="px-5 py-3 space-y-1">
        {batches.map((batch) => {
          const Icon = getBatchIcon(batch.name);
          return (
            <div
              key={batch.name}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-xl transition-all duration-300',
                batch.status === 'active' && 'bg-blue-50 dark:bg-blue-500/5 border border-blue-100 dark:border-blue-500/10',
                batch.status === 'done' && 'opacity-70',
                batch.status === 'pending' && 'opacity-40',
              )}
            >
              {/* Status icon */}
              <div className="shrink-0">
                {batch.status === 'done' ? (
                  <div className="w-5 h-5 rounded-full bg-emerald-100 dark:bg-emerald-500/15 flex items-center justify-center">
                    <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                  </div>
                ) : batch.status === 'active' ? (
                  <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />
                ) : (
                  <div className="w-5 h-5 rounded-full border border-slate-200 dark:border-white/10" />
                )}
              </div>

              {/* Batch name */}
              <div className="flex-1 min-w-0">
                <span className={cn(
                  'text-[13px] font-medium',
                  batch.status === 'active' ? 'text-blue-700 dark:text-blue-300' :
                  batch.status === 'done' ? 'text-slate-600 dark:text-white/50' :
                  'text-slate-400 dark:text-white/25'
                )}>
                  {batch.name}
                </span>
              </div>

              {/* File count */}
              {batch.files.length > 0 && (
                <span className="text-[10px] text-slate-400 dark:text-white/25 font-mono">
                  {batch.files.length} files
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Files created (collapsible) */}
      {allFiles.length > 0 && (
        <div className="border-t border-slate-100 dark:border-white/[0.05]">
          <button
            type="button"
            onClick={() => setShowFiles(!showFiles)}
            className="w-full flex items-center gap-2 px-5 py-2.5 text-[11px] font-bold text-slate-400 dark:text-white/30 uppercase tracking-wider hover:text-slate-600 dark:hover:text-white/50 transition-colors"
          >
            {showFiles ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Files Created ({allFiles.length})
          </button>
          {showFiles && (
            <div className="px-5 pb-3 max-h-40 overflow-y-auto custom-scrollbar">
              <div className="space-y-0.5">
                {allFiles.map((file, i) => (
                  <div key={i} className="flex items-center gap-2 py-0.5">
                    <FileCode2 className="w-3 h-3 text-slate-300 dark:text-white/15 shrink-0" />
                    <span className="text-[11px] text-slate-500 dark:text-white/35 font-mono truncate">
                      {file}
                    </span>
                  </div>
                ))}
                <div ref={filesEndRef} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
