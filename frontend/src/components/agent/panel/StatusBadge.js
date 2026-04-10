'use client';

// ─────────────────────────────────────────────────────────
//  StatusBadge — Colored status dot + label for AgentPanel
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';

export const STATUS_CONFIG = {
  idle:       { dot: 'bg-slate-400',                text: 'text-slate-500',   label: 'Idle' },
  connecting: { dot: 'bg-amber-500 animate-pulse',  text: 'text-amber-600',   label: 'Connecting...' },
  preparing:  { dot: 'bg-amber-500 animate-pulse',  text: 'text-amber-600',   label: 'Preparing...' },
  connected:  { dot: 'bg-emerald-500',              text: 'text-emerald-600', label: 'Connected' },
  ready:      { dot: 'bg-emerald-500',              text: 'text-emerald-600', label: 'Ready' },
  running:    { dot: 'bg-blue-500 animate-pulse',   text: 'text-blue-600',    label: 'Working...' },
  working:    { dot: 'bg-blue-500 animate-pulse',   text: 'text-blue-600',    label: 'Working...' },
  error:      { dot: 'bg-red-500',                  text: 'text-red-600',     label: 'Error' },
  stopped:    { dot: 'bg-slate-400',                text: 'text-slate-500',   label: 'Stopped' },
};

export function getStatusConfig(state) {
  return STATUS_CONFIG[state] || STATUS_CONFIG.idle;
}

export default function StatusBadge({ state = 'idle', size = 'sm', className }) {
  const config = getStatusConfig(state);
  const dotSize   = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2';
  const textClass = size === 'sm'
    ? 'text-[10px] font-bold uppercase tracking-wider'
    : 'text-[11px] font-bold uppercase tracking-wider';

  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <div className={cn('rounded-full', dotSize, config.dot)} />
      <span className={cn(textClass, config.text)}>{config.label}</span>
    </div>
  );
}
