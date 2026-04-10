'use client';

// ─────────────────────────────────────────────────────────
//  ConnectionStatus — small pill showing WS connection state
//  Extracted from workspace/[projectId]/page.js
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';

export default function ConnectionStatus({ status, error }) {
  const config = {
    idle:         { color: 'text-slate-400 dark:text-slate-500',   label: 'Idle' },
    connecting:   { color: 'text-blue-600 dark:text-blue-400',     label: 'Connecting...' },
    cloning:      { color: 'text-violet-600 dark:text-violet-400', label: 'Cloning repository...' },
    installing:   { color: 'text-violet-600 dark:text-violet-400', label: 'Installing dependencies...' },
    starting:     { color: 'text-violet-600 dark:text-violet-400', label: 'Starting dev server...' },
    health_check: { color: 'text-violet-600 dark:text-violet-400', label: 'Checking preview...' },
    preparing:    { color: 'text-amber-600 dark:text-amber-400',   label: 'Preparing workspace...' },
    running:      { color: 'text-blue-600 dark:text-blue-400',     label: 'Agent working…' },
    ready:        { color: 'text-emerald-600 dark:text-emerald-400', label: 'Ready' },
    connected:    { color: 'text-emerald-600 dark:text-emerald-400', label: 'Connected' },
    error:        { color: 'text-red-600 dark:text-red-400',       label: 'Error' },
    stopped:      { color: 'text-slate-400 dark:text-slate-500',   label: 'Stopped' },
  };
  const current = config[status] || config.idle;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full shadow-sm">
      <div
        className={cn(
          'w-2 h-2 rounded-full',
          status === 'ready' || status === 'connected'
            ? 'bg-emerald-500'
            : status === 'preparing'
              ? 'bg-amber-500 animate-pulse'
              : status === 'cloning' || status === 'installing' || status === 'starting' || status === 'health_check'
                ? 'bg-violet-500 animate-pulse'
                : status === 'connecting' || status === 'running'
                  ? 'bg-blue-500 animate-pulse'
                  : status === 'error'
                    ? 'bg-red-500'
                    : 'bg-slate-300 dark:bg-slate-600',
        )}
      />
      <span className={cn('text-xs font-semibold', current.color)}>
        {error || current.label}
      </span>
    </div>
  );
}
