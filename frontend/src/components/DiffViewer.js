'use client';

// ─────────────────────────────────────────────────────────
//  DiffViewer
//
//  Git-style unified inline diff for one file. Consumes
//  { previous, current } strings (snapshots captured by
//  useAgentSession on each file_write_event) and renders:
//    • '+' green-highlighted lines for additions
//    • '-' red-highlighted lines for deletions
//    • unchanged-line context, collapsed by default to keep
//      large files readable (click to expand)
//
//  Pure presentational — no fetching, no side effects.
// ─────────────────────────────────────────────────────────

import { useMemo, useState } from 'react';
import { diffLines } from 'diff';
import { Copy, Check, FileCode2, Plus, Minus, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';

const CONTEXT_LINES = 3;          // unchanged lines kept around each hunk
const COLLAPSE_THRESHOLD = 8;     // collapse runs of unchanged > this many lines

/**
 * Convert diffLines() output → an array of "groups" suitable for rendering.
 * Each group is either:
 *   { kind: 'changed', lines: [...] } — contiguous +/- block (always shown)
 *   { kind: 'context', lines: [...] } — unchanged block, possibly collapsible
 */
function buildGroups(parts) {
  const groups = [];
  for (const part of parts) {
    const lines = part.value.split('\n');
    // The trailing empty string from a terminating \n is meaningless noise.
    if (lines.length && lines[lines.length - 1] === '') lines.pop();
    if (!lines.length) continue;

    if (part.added || part.removed) {
      const sign = part.added ? '+' : '-';
      groups.push({ kind: 'changed', sign, lines });
    } else {
      groups.push({ kind: 'context', lines });
    }
  }
  return groups;
}

function CollapsibleContext({ lines, isFirst, isLast, expanded, onToggle }) {
  // Always show the first/last `CONTEXT_LINES` of unchanged content surrounding
  // a hunk; collapse the long middle into a clickable banner.
  const total = lines.length;
  if (expanded || total <= COLLAPSE_THRESHOLD) {
    return (
      <>
        {lines.map((line, i) => (
          <div key={i} className="flex font-mono text-[12px] leading-[20px] text-slate-500 dark:text-slate-400">
            <span className="w-6 text-center select-none opacity-30"> </span>
            <span className="flex-1 px-3 whitespace-pre-wrap break-all">{line || ' '}</span>
          </div>
        ))}
      </>
    );
  }

  const head = isFirst ? [] : lines.slice(0, CONTEXT_LINES);
  const tail = isLast  ? [] : lines.slice(-CONTEXT_LINES);
  const hiddenCount = total - head.length - tail.length;

  return (
    <>
      {head.map((line, i) => (
        <div key={`h${i}`} className="flex font-mono text-[12px] leading-[20px] text-slate-500 dark:text-slate-400">
          <span className="w-6 text-center select-none opacity-30"> </span>
          <span className="flex-1 px-3 whitespace-pre-wrap break-all">{line || ' '}</span>
        </div>
      ))}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 text-[11px] font-medium text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.03] border-y border-dashed border-slate-200 dark:border-[#21262d]"
      >
        <ChevronDown className="w-3 h-3" />
        Show {hiddenCount} unchanged line{hiddenCount === 1 ? '' : 's'}
      </button>
      {tail.map((line, i) => (
        <div key={`t${i}`} className="flex font-mono text-[12px] leading-[20px] text-slate-500 dark:text-slate-400">
          <span className="w-6 text-center select-none opacity-30"> </span>
          <span className="flex-1 px-3 whitespace-pre-wrap break-all">{line || ' '}</span>
        </div>
      ))}
    </>
  );
}

export default function DiffViewer({
  filename,
  previous = '',
  current = '',
  truncated = false,
  metrics = null,    // { writes, lastAction, lastPhase, elapsedMs, lastSize }
  onClose = null,
}) {
  const [expandedAll, setExpandedAll] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState({});
  const [copied, setCopied] = useState(false);

  const { groups, addedCount, removedCount, isNew } = useMemo(() => {
    if (current && !previous) {
      // First write — entire file is new content; no diff needed.
      const lines = current.split('\n');
      if (lines.length && lines[lines.length - 1] === '') lines.pop();
      return {
        groups: [{ kind: 'changed', sign: '+', lines }],
        addedCount: lines.length,
        removedCount: 0,
        isNew: true,
      };
    }
    const parts = diffLines(previous || '', current || '');
    const grouped = buildGroups(parts);
    let added = 0;
    let removed = 0;
    for (const g of grouped) {
      if (g.kind === 'changed') {
        if (g.sign === '+') added += g.lines.length;
        else removed += g.lines.length;
      }
    }
    return { groups: grouped, addedCount: added, removedCount: removed, isNew: false };
  }, [previous, current]);

  const noChanges = !isNew && addedCount === 0 && removedCount === 0;

  return (
    <div className="h-full flex flex-col bg-white dark:bg-[#0d1117]">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2.5 border-b border-slate-200 dark:border-[#21262d]">
        <div className="flex items-center gap-2 min-w-0">
          <FileCode2 className="w-4 h-4 text-slate-400 shrink-0" />
          <span className="text-[13px] font-mono text-slate-700 dark:text-slate-200 truncate">
            {filename || 'Untitled'}
          </span>
          {isNew && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">
              New
            </span>
          )}
          {truncated && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400 uppercase tracking-wider" title="File >256KB — diff suppressed">
              Truncated
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {/* Stats */}
          {!noChanges && (
            <div className="flex items-center gap-2 text-[11px] font-mono">
              <span className="flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400">
                <Plus className="w-3 h-3" /> {addedCount}
              </span>
              <span className="flex items-center gap-0.5 text-red-500 dark:text-red-400">
                <Minus className="w-3 h-3" /> {removedCount}
              </span>
            </div>
          )}

          {/* Metrics badges */}
          {metrics && (
            <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
              {metrics.writes > 1 && (
                <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800" title="Total writes to this file in this session">
                  {metrics.writes}× writes
                </span>
              )}
              {typeof metrics.lastSize === 'number' && (
                <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800">
                  {metrics.lastSize > 1024 ? `${(metrics.lastSize / 1024).toFixed(1)}KB` : `${metrics.lastSize}B`}
                </span>
              )}
              {metrics.lastPhase && (
                <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-500" title="Pipeline phase">
                  {metrics.lastPhase}
                </span>
              )}
            </div>
          )}

          {/* Expand all / Copy / Close */}
          <button
            onClick={() => setExpandedAll((v) => !v)}
            title={expandedAll ? 'Collapse unchanged' : 'Expand all'}
            className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-white/[0.06] text-slate-400"
          >
            {expandedAll ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={() => {
              navigator.clipboard.writeText(current || '');
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            }}
            title="Copy file content"
            className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-white/[0.06] text-slate-400"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="px-2 py-1 rounded text-[12px] text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06]"
            >
              Close
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto bg-slate-50/50 dark:bg-[#0d1117] custom-scrollbar">
        {truncated && !current ? (
          <div className="p-8 text-center text-sm text-slate-500">
            This file is too large to inline (&gt;256 KB). Open it directly to view contents.
          </div>
        ) : noChanges ? (
          <div className="p-8 text-center text-sm text-slate-500">
            No changes detected on this write.
          </div>
        ) : (
          <div className="py-1">
            {groups.map((g, idx) => {
              const key = `g${idx}`;
              if (g.kind === 'changed') {
                const bg = g.sign === '+'
                  ? 'bg-emerald-50/70 dark:bg-emerald-500/[0.07]'
                  : 'bg-red-50/70 dark:bg-red-500/[0.07]';
                const fg = g.sign === '+'
                  ? 'text-emerald-700 dark:text-emerald-300'
                  : 'text-red-600 dark:text-red-300';
                return g.lines.map((line, i) => (
                  <div
                    key={`${key}-${i}`}
                    className={cn('flex font-mono text-[12px] leading-[20px]', bg, fg)}
                  >
                    <span className="w-6 text-center select-none font-bold">{g.sign}</span>
                    <span className="flex-1 px-3 whitespace-pre-wrap break-all">{line || ' '}</span>
                  </div>
                ));
              }
              return (
                <CollapsibleContext
                  key={key}
                  lines={g.lines}
                  isFirst={idx === 0}
                  isLast={idx === groups.length - 1}
                  expanded={expandedAll || expandedGroups[idx]}
                  onToggle={() => setExpandedGroups((s) => ({ ...s, [idx]: !s[idx] }))}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
