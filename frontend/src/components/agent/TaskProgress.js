'use client';

// ─────────────────────────────────────────────────────────
//  TaskProgress — Vertical pipeline stepper/timeline
//  Renders task_phase events as an animated progress bar
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';
import {
  CheckCircle2, Circle, Loader2, XCircle,
  ShieldCheck, FolderGit2, Brain, Search, Code2, Hammer, GitPullRequestArrow,
} from 'lucide-react';

const PHASE_ICONS = {
  1: ShieldCheck,       // Validate
  2: FolderGit2,        // Clone / workspace
  3: Brain,             // Classify
  4: Search,            // Explore
  5: Code2,             // Write code
  6: Hammer,            // Verify build
  7: GitPullRequestArrow, // Push
};

const PHASE_COLORS = {
  active: {
    icon: 'text-blue-500',
    line: 'bg-blue-200',
    bg: 'bg-blue-50',
    border: 'border-blue-200',
    text: 'text-blue-700',
    desc: 'text-blue-500',
  },
  done: {
    icon: 'text-emerald-500',
    line: 'bg-emerald-300',
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    text: 'text-slate-700',
    desc: 'text-slate-500',
  },
  error: {
    icon: 'text-red-500',
    line: 'bg-red-200',
    bg: 'bg-red-50',
    border: 'border-red-200',
    text: 'text-red-700',
    desc: 'text-red-500',
  },
  pending: {
    icon: 'text-slate-300',
    line: 'bg-slate-200',
    bg: 'bg-slate-50',
    border: 'border-slate-200',
    text: 'text-slate-400',
    desc: 'text-slate-400',
  },
};

/**
 * TaskProgress — Renders a vertical stepper showing pipeline phases.
 *
 * @param {Array} phases - Array of { phase, title, description, status }
 */
export default function TaskProgress({ phases = [] }) {
  if (phases.length === 0) return null;

  return (
    <div className="px-4 py-3">
      <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
            Pipeline Progress
          </span>
        </div>

        <div className="space-y-0">
          {phases.map((phase, idx) => {
            const isLast = idx === phases.length - 1;
            const colors = PHASE_COLORS[phase.status] || PHASE_COLORS.pending;
            const PhaseIcon = PHASE_ICONS[phase.phase] || Circle;

            return (
              <div key={phase.phase} className="flex gap-3">
                {/* Timeline column */}
                <div className="flex flex-col items-center">
                  {/* Icon circle */}
                  <div className={cn(
                    "w-8 h-8 rounded-xl flex items-center justify-center border shrink-0 transition-all duration-300",
                    colors.bg, colors.border,
                  )}>
                    {phase.status === 'active' ? (
                      <Loader2 className={cn("w-4 h-4 animate-spin", colors.icon)} />
                    ) : phase.status === 'done' ? (
                      <CheckCircle2 className={cn("w-4 h-4", colors.icon)} />
                    ) : phase.status === 'error' ? (
                      <XCircle className={cn("w-4 h-4", colors.icon)} />
                    ) : (
                      <PhaseIcon className={cn("w-4 h-4", colors.icon)} />
                    )}
                  </div>
                  {/* Connector line */}
                  {!isLast && (
                    <div className={cn(
                      "w-0.5 h-6 transition-colors duration-500",
                      colors.line,
                    )} />
                  )}
                </div>

                {/* Content */}
                <div className="pb-4 pt-1 min-w-0">
                  <p className={cn(
                    "text-xs font-semibold leading-tight transition-colors",
                    colors.text,
                  )}>
                    {phase.title}
                  </p>
                  {phase.description && (
                    <p className={cn(
                      "text-[11px] mt-0.5 leading-snug transition-colors",
                      colors.desc,
                    )}>
                      {phase.description}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
