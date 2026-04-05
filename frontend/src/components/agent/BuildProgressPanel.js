'use client';

// ─────────────────────────────────────────────────────────
//  BuildProgressPanel — Right-panel build progress view
//  Shows animated batch progress, live file list, mini tree
//  during project generation. Base44-inspired design.
// ─────────────────────────────────────────────────────────

import { useState, useEffect, useRef, useMemo } from 'react';
import { cn } from '@/lib/utils';
import {
  Check, Loader2, FileCode2, Layers, Sparkles,
  Clock, Package, FolderOpen, Folder, FileText,
  ChevronRight, ChevronDown, Zap, Rocket,
  FileCode, FileJson, Settings2, Image as ImageIcon, FileType,
} from 'lucide-react';

// ── Batch config ──────────────────────────────────────────
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
  if (/integration/i.test(batch)) return Zap;
  return FileCode2;
}

function getBatchColor(batch) {
  if (/foundation/i.test(batch)) return { bg: 'bg-violet-500', light: 'bg-violet-50 dark:bg-violet-500/10', border: 'border-violet-200 dark:border-violet-500/20', text: 'text-violet-600 dark:text-violet-400' };
  if (/component|ui/i.test(batch)) return { bg: 'bg-blue-500', light: 'bg-blue-50 dark:bg-blue-500/10', border: 'border-blue-200 dark:border-blue-500/20', text: 'text-blue-600 dark:text-blue-400' };
  if (/layout/i.test(batch)) return { bg: 'bg-amber-500', light: 'bg-amber-50 dark:bg-amber-500/10', border: 'border-amber-200 dark:border-amber-500/20', text: 'text-amber-600 dark:text-amber-400' };
  if (/page/i.test(batch)) return { bg: 'bg-emerald-500', light: 'bg-emerald-50 dark:bg-emerald-500/10', border: 'border-emerald-200 dark:border-emerald-500/20', text: 'text-emerald-600 dark:text-emerald-400' };
  if (/integration/i.test(batch)) return { bg: 'bg-rose-500', light: 'bg-rose-50 dark:bg-rose-500/10', border: 'border-rose-200 dark:border-rose-500/20', text: 'text-rose-600 dark:text-rose-400' };
  return { bg: 'bg-slate-500', light: 'bg-slate-50 dark:bg-slate-500/10', border: 'border-slate-200 dark:border-slate-500/20', text: 'text-slate-600 dark:text-slate-400' };
}

// ── File icon helper ──────────────────────────────────────
function getFileIcon(name) {
  const ext = name.split('.').pop()?.toLowerCase();
  const map = {
    js:   { icon: FileCode, color: 'text-amber-500' },
    jsx:  { icon: FileCode, color: 'text-blue-500' },
    ts:   { icon: FileCode, color: 'text-blue-600' },
    tsx:  { icon: FileCode, color: 'text-blue-600' },
    json: { icon: FileJson, color: 'text-amber-500' },
    css:  { icon: FileType, color: 'text-purple-500' },
    md:   { icon: FileText, color: 'text-slate-400' },
    html: { icon: FileCode, color: 'text-orange-500' },
    yml:  { icon: Settings2, color: 'text-rose-500' },
    yaml: { icon: Settings2, color: 'text-rose-500' },
    png:  { icon: ImageIcon, color: 'text-emerald-500' },
    jpg:  { icon: ImageIcon, color: 'text-emerald-500' },
    svg:  { icon: ImageIcon, color: 'text-amber-500' },
    env:  { icon: Settings2, color: 'text-slate-400' },
  };
  return map[ext] || { icon: FileText, color: 'text-slate-400' };
}

// ── Mini Tree Node ────────────────────────────────────────
function MiniTreeNode({ node, depth = 0, recentFiles = [] }) {
  const [isOpen, setIsOpen] = useState(depth < 2);
  const isDir = node.type === 'dir';
  const isRecent = !isDir && recentFiles.includes(node.path);
  const { icon: FIcon, color: fColor } = getFileIcon(node.name);

  return (
    <div>
      <button
        onClick={() => isDir && setIsOpen(!isOpen)}
        className={cn(
          "w-full flex items-center gap-1.5 py-[3px] px-1 text-left text-[11px] rounded transition-all",
          isRecent
            ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 font-medium"
            : "text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.04]",
        )}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {isDir ? (
          isOpen
            ? <ChevronDown className="w-3 h-3 text-slate-400 shrink-0" />
            : <ChevronRight className="w-3 h-3 text-slate-400 shrink-0" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {isDir ? (
          isOpen
            ? <FolderOpen className="w-3.5 h-3.5 text-amber-500 shrink-0" />
            : <Folder className="w-3.5 h-3.5 text-amber-500 shrink-0" />
        ) : (
          <FIcon className={cn("w-3.5 h-3.5 shrink-0", fColor)} />
        )}
        <span className="truncate">{node.name}</span>
        {isRecent && (
          <Sparkles className="w-3 h-3 text-emerald-500 shrink-0 animate-pulse ml-auto" />
        )}
      </button>
      {isDir && isOpen && node.children && (
        <div>
          {node.children
            .sort((a, b) => {
              const aDir = a.type === 'dir';
              const bDir = b.type === 'dir';
              if (aDir !== bDir) return aDir ? -1 : 1;
              return a.name.localeCompare(b.name);
            })
            .map((child) => (
              <MiniTreeNode key={child.path || child.name} node={child} depth={depth + 1} recentFiles={recentFiles} />
            ))}
        </div>
      )}
    </div>
  );
}

// ── Build tree from file paths ────────────────────────────
function buildTreeFromPaths(paths) {
  const root = [];
  paths.forEach((p) => {
    const parts = p.replace(/^\/+/, '').split('/');
    let current = root;
    let pathSoFar = '';
    parts.forEach((part, i) => {
      pathSoFar += (pathSoFar ? '/' : '') + part;
      const existing = current.find((n) => n.name === part);
      if (existing) {
        current = existing.children || [];
      } else {
        const isFile = i === parts.length - 1;
        const node = {
          name: part,
          path: pathSoFar,
          type: isFile ? 'file' : 'dir',
          ...(isFile ? {} : { children: [] }),
        };
        current.push(node);
        if (!isFile) current = node.children;
      }
    });
  });
  return root;
}


/**
 * BuildProgressPanel — Right panel during generation.
 *
 * Displays:
 *  1. Overall progress bar with percentage + time estimate
 *  2. Batch-by-batch progress with file counts
 *  3. Live file list with "just created" animations
 *  4. Mini file tree growing as files are generated
 */
export default function BuildProgressPanel({ isVisible = false, onComplete }) {
  const [percentage, setPercentage] = useState(0);
  const [currentBatch, setCurrentBatch] = useState('');
  const [currentMessage, setCurrentMessage] = useState('Preparing workspace...');
  const [batches, setBatches] = useState([]);
  const [allFiles, setAllFiles] = useState([]);
  const [recentFiles, setRecentFiles] = useState([]);
  const [isComplete, setIsComplete] = useState(false);
  const [totalFiles, setTotalFiles] = useState(0);
  const [startTime] = useState(Date.now());
  const [activeSection, setActiveSection] = useState('progress'); // progress | files | tree
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

  // Auto-scroll file list
  useEffect(() => {
    if (activeSection === 'files' && filesEndRef.current) {
      filesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [allFiles, activeSection]);

  // Listen for WebSocket messages
  useEffect(() => {
    if (!isVisible) return;

    const handleProgress = (e) => {
      const { batch, message, files, percentage: pct } = e.detail;
      setPercentage(pct || 0);
      setCurrentBatch(batch || '');
      setCurrentMessage(message || '');

      setBatches(prev => prev.map(b => {
        if (b.name === batch) {
          return { ...b, status: 'active', files: [...b.files, ...(files || [])] };
        }
        return b;
      }));

      if (files?.length) {
        setAllFiles(prev => [...prev, ...files.filter(f => !prev.includes(f))]);
        setRecentFiles(files);
        // Clear "recent" glow after 3 seconds
        setTimeout(() => setRecentFiles([]), 3000);
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
      setBatches(prev => prev.map(b => ({ ...b, status: 'done' })));
      onComplete?.();
    };

    window.addEventListener('generation_progress', handleProgress);
    window.addEventListener('batch_complete', handleBatchComplete);
    window.addEventListener('generation_complete', handleComplete);

    return () => {
      window.removeEventListener('generation_progress', handleProgress);
      window.removeEventListener('batch_complete', handleBatchComplete);
      window.removeEventListener('generation_complete', handleComplete);
    };
  }, [isVisible, allFiles, onComplete]);

  // Time estimate
  const elapsed = (Date.now() - startTime) / 1000;
  const estimatedTotal = percentage > 5 ? (elapsed / percentage) * 100 : 0;
  const remaining = Math.max(0, estimatedTotal - elapsed);
  const remainingMin = Math.floor(remaining / 60);
  const remainingSec = Math.floor(remaining % 60);

  // Build file tree
  const fileTree = useMemo(() => buildTreeFromPaths(allFiles), [allFiles]);

  if (!isVisible) return null;

  return (
    <div className="h-full flex flex-col bg-white dark:bg-[#0f1118] overflow-hidden">

      {/* ── Header ────────────────────────────────────── */}
      <div className={cn(
        "shrink-0 px-5 py-4 border-b transition-colors",
        isComplete
          ? "bg-emerald-50 dark:bg-emerald-500/5 border-emerald-100 dark:border-emerald-500/10"
          : "bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-500/5 dark:to-indigo-500/5 border-slate-200 dark:border-white/[0.06]"
      )}>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            {isComplete ? (
              <div className="w-10 h-10 rounded-xl bg-emerald-100 dark:bg-emerald-500/15 flex items-center justify-center">
                <Rocket className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
              </div>
            ) : (
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-md shadow-blue-500/20">
                <Sparkles className="w-5 h-5 text-white animate-pulse" />
              </div>
            )}
            <div>
              <h3 className={cn(
                "text-sm font-bold",
                isComplete ? "text-emerald-800 dark:text-emerald-200" : "text-slate-900 dark:text-white"
              )}>
                {isComplete ? 'Project Ready!' : 'Building Your Project'}
              </h3>
              <p className="text-[11px] text-slate-500 dark:text-white/40 mt-0.5 max-w-[220px] truncate">
                {currentMessage}
              </p>
            </div>
          </div>

          {/* Time estimate */}
          {!isComplete && percentage > 5 && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 bg-white dark:bg-white/[0.06] rounded-lg border border-slate-200 dark:border-white/[0.08] shadow-sm">
              <Clock className="w-3.5 h-3.5 text-slate-400 dark:text-white/30" />
              <span className="text-[11px] font-mono font-bold text-slate-600 dark:text-white/50">
                ~{remainingMin > 0 ? `${remainingMin}m ` : ''}{remainingSec}s
              </span>
            </div>
          )}
        </div>

        {/* Progress bar */}
        <div className="relative h-2.5 bg-slate-100 dark:bg-white/[0.06] rounded-full overflow-hidden">
          <div
            className={cn(
              'absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out',
              isComplete
                ? 'bg-gradient-to-r from-emerald-400 to-emerald-500'
                : 'bg-gradient-to-r from-blue-500 via-indigo-500 to-violet-500'
            )}
            style={{ width: `${percentage}%` }}
          />
          {!isComplete && (
            <div
              className="absolute inset-y-0 rounded-full bg-white/25 animate-pulse"
              style={{ left: `${Math.max(0, percentage - 10)}%`, width: '10%' }}
            />
          )}
        </div>
        <div className="flex items-center justify-between mt-2">
          <span className={cn(
            "text-xs font-bold",
            isComplete ? "text-emerald-600 dark:text-emerald-400" : "text-blue-600 dark:text-blue-400"
          )}>
            {Math.round(percentage)}%
          </span>
          <span className="text-[11px] font-medium text-slate-400 dark:text-white/30">
            {allFiles.length} file{allFiles.length !== 1 ? 's' : ''} created
          </span>
        </div>
      </div>

      {/* ── Section Tabs ──────────────────────────────── */}
      <div className="shrink-0 flex items-center gap-1 px-4 py-2 border-b border-slate-100 dark:border-white/[0.05] bg-slate-50/50 dark:bg-white/[0.02]">
        {[
          { id: 'progress', label: 'Phases', icon: Layers },
          { id: 'files', label: `Files (${allFiles.length})`, icon: FileCode2 },
          { id: 'tree', label: 'Tree', icon: FolderOpen },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveSection(tab.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all",
              activeSection === tab.id
                ? "bg-white dark:bg-white/[0.08] text-slate-800 dark:text-white border border-slate-200 dark:border-white/[0.1] shadow-sm"
                : "text-slate-400 dark:text-white/30 hover:text-slate-600 dark:hover:text-white/50"
            )}
          >
            <tab.icon className="w-3 h-3" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Content ───────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">

        {/* PHASES section */}
        {activeSection === 'progress' && (
          <div className="px-4 py-4 space-y-2">
            {batches.map((batch, idx) => {
              const Icon = getBatchIcon(batch.name);
              const colors = getBatchColor(batch.name);
              const isActive = batch.status === 'active';
              const isDone = batch.status === 'done';

              return (
                <div
                  key={batch.name}
                  className={cn(
                    "flex items-center gap-3 px-4 py-3 rounded-xl border transition-all duration-500",
                    isActive && `${colors.light} ${colors.border} shadow-sm`,
                    isDone && "bg-slate-50 dark:bg-white/[0.02] border-slate-100 dark:border-white/[0.04]",
                    !isActive && !isDone && "border-transparent opacity-40",
                  )}
                >
                  {/* Status icon */}
                  <div className={cn(
                    "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-all",
                    isDone && "bg-emerald-100 dark:bg-emerald-500/15",
                    isActive && `${colors.light}`,
                    !isDone && !isActive && "bg-slate-100 dark:bg-white/[0.04]",
                  )}>
                    {isDone ? (
                      <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                    ) : isActive ? (
                      <Loader2 className={cn("w-4 h-4 animate-spin", colors.text)} />
                    ) : (
                      <Icon className="w-4 h-4 text-slate-300 dark:text-white/20" />
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <span className={cn(
                      "text-[13px] font-bold",
                      isDone ? "text-slate-600 dark:text-white/50" :
                      isActive ? colors.text :
                      "text-slate-400 dark:text-white/25"
                    )}>
                      {batch.name}
                    </span>
                    {isActive && currentMessage && (
                      <p className={cn("text-[10px] mt-0.5 truncate", colors.text, "opacity-70")}>
                        {currentMessage}
                      </p>
                    )}
                  </div>

                  {/* File count badge */}
                  {batch.files.length > 0 && (
                    <div className={cn(
                      "px-2 py-0.5 rounded-full text-[10px] font-bold",
                      isDone
                        ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                        : `${colors.light} ${colors.text}`
                    )}>
                      {batch.files.length} files
                    </div>
                  )}
                </div>
              );
            })}

            {/* Completion card */}
            {isComplete && (
              <div className="mt-4 p-4 bg-gradient-to-br from-emerald-50 to-green-50 dark:from-emerald-500/5 dark:to-green-500/5 border border-emerald-200 dark:border-emerald-500/20 rounded-2xl animate-in fade-in zoom-in-95 duration-500">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 rounded-xl bg-emerald-500 flex items-center justify-center shadow-md shadow-emerald-500/20">
                    <Check className="w-5 h-5 text-white" />
                  </div>
                  <div>
                    <p className="text-sm font-bold text-emerald-800 dark:text-emerald-200">Generation Complete!</p>
                    <p className="text-xs text-emerald-600 dark:text-emerald-400 mt-0.5">
                      {totalFiles || allFiles.length} files created successfully
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
                  <Clock className="w-3.5 h-3.5" />
                  <span>Completed in {Math.floor(elapsed / 60)}m {Math.floor(elapsed % 60)}s</span>
                </div>
              </div>
            )}
          </div>
        )}

        {/* FILES section — live file list */}
        {activeSection === 'files' && (
          <div className="p-3 space-y-0.5">
            {allFiles.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <FileCode2 className="w-8 h-8 text-slate-300 dark:text-white/10 mb-3" />
                <p className="text-xs text-slate-400 dark:text-white/30">Files will appear here as they're generated...</p>
              </div>
            ) : (
              allFiles.map((file, i) => {
                const fileName = file.split('/').pop();
                const dirPath = file.split('/').slice(0, -1).join('/');
                const { icon: FIcon, color: fColor } = getFileIcon(fileName);
                const isRecent = recentFiles.includes(file);

                return (
                  <div
                    key={i}
                    className={cn(
                      "flex items-center gap-2.5 px-3 py-2 rounded-lg transition-all duration-500",
                      isRecent
                        ? "bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 animate-in fade-in slide-in-from-left-2 duration-300"
                        : "hover:bg-slate-50 dark:hover:bg-white/[0.03]"
                    )}
                  >
                    <FIcon className={cn("w-4 h-4 shrink-0", isRecent ? "text-emerald-500" : fColor)} />
                    <div className="flex-1 min-w-0">
                      <span className={cn(
                        "text-[12px] font-medium block truncate",
                        isRecent ? "text-emerald-700 dark:text-emerald-300" : "text-slate-700 dark:text-slate-300"
                      )}>
                        {fileName}
                      </span>
                      {dirPath && (
                        <span className="text-[10px] text-slate-400 dark:text-white/25 truncate block">
                          {dirPath}
                        </span>
                      )}
                    </div>
                    {isRecent && (
                      <Sparkles className="w-3.5 h-3.5 text-emerald-500 animate-pulse shrink-0" />
                    )}
                  </div>
                );
              })
            )}
            <div ref={filesEndRef} />
          </div>
        )}

        {/* TREE section — mini file tree */}
        {activeSection === 'tree' && (
          <div className="p-3">
            {fileTree.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <FolderOpen className="w-8 h-8 text-slate-300 dark:text-white/10 mb-3" />
                <p className="text-xs text-slate-400 dark:text-white/30">Project tree will grow here...</p>
              </div>
            ) : (
              fileTree.map((node) => (
                <MiniTreeNode key={node.path || node.name} node={node} recentFiles={recentFiles} />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
