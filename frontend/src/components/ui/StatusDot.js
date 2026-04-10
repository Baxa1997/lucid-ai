'use client';

// ─────────────────────────────────────────────────────────
//  StatusDot — Colored animated status indicator
//  Used by AgentPanel, AgentWorkspace
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';

/**
 * @param {'idle'|'connecting'|'preparing'|'connected'|'ready'|'running'|'working'|'error'|'stopped'} status
 * @param {boolean} showLabel
 * @param {'sm'|'md'} size
 */
export const STATUS_CONFIG = {
  idle:       { dot: 'bg-slate-400',                      text: 'text-slate-500',   label: 'Idle' },
  connecting: { dot: 'bg-amber-500 animate-pulse',        text: 'text-amber-600',   label: 'Connecting...' },
  preparing:  { dot: 'bg-amber-500 animate-pulse',        text: 'text-amber-600',   label: 'Preparing...' },
  connected:  { dot: 'bg-emerald-500',                    text: 'text-emerald-600', label: 'Connected' },
  ready:      { dot: 'bg-emerald-500',                    text: 'text-emerald-600', label: 'Ready' },
  running:    { dot: 'bg-blue-500 animate-pulse',         text: 'text-blue-600',    label: 'Working...' },
  working:    { dot: 'bg-blue-500 animate-pulse',         text: 'text-blue-600',    label: 'Working...' },
  error:      { dot: 'bg-red-500',                        text: 'text-red-600',     label: 'Error' },
  stopped:    { dot: 'bg-slate-400',                      text: 'text-slate-500',   label: 'Stopped' },
};

export default function StatusDot({ status = 'idle', showLabel = true, size = 'sm', className }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.idle;
  const dotSize = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2';
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-[11px]';

  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <div className={cn('rounded-full', dotSize, config.dot)} />
      {showLabel && (
        <span className={cn('font-bold uppercase tracking-wider', textSize, config.text)}>
          {config.label}
        </span>
      )}
    </div>
  );
}
