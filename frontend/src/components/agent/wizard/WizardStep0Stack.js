'use client';

// ─────────────────────────────────────────────────────────
//  WizardStep0Stack — Choose your tech stack
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';
import { Globe, Sparkles, CheckCircle2 } from 'lucide-react';
import { StackLogos, STACKS } from './StackLogos';

export default function WizardStep0Stack({ stack, onUpdate }) {
  return (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[720px]">
        <div className="text-center mb-8">
          <h2 className="text-[28px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">What would you like to build?</h2>
          <p className="text-[15px] text-slate-400 dark:text-slate-500 mt-2">Choose a stack to get started. We'll set up everything for you.</p>
        </div>

        {/* "Choose for me" — default, prominent, ABOVE the grid */}
        <button
          onClick={() => onUpdate({ stack: 'auto' })}
          className={cn(
            'group relative flex items-center gap-4 px-5 py-4 rounded-2xl text-left transition-all duration-200 w-full mb-4',
            stack === 'auto'
              ? 'bg-violet-50/80 dark:bg-violet-500/10 border-2 border-violet-400 dark:border-violet-500 shadow-[0_0_0_3px_rgba(139,92,246,0.08)]'
              : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-violet-300 dark:hover:border-violet-600 hover:-translate-y-[1px]',
          )}
        >
          <div className={cn('w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-colors',
            stack === 'auto' ? 'bg-gradient-to-br from-violet-100 to-blue-100 dark:from-violet-500/20 dark:to-blue-500/20' : 'bg-slate-50 dark:bg-slate-700/80',
          )}>
            <Sparkles className={cn('w-5 h-5', stack === 'auto' ? 'text-violet-600 dark:text-violet-400' : 'text-slate-400 group-hover:text-violet-400')} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className={cn('text-[14px] font-semibold', stack === 'auto' ? 'text-violet-700 dark:text-violet-400' : 'text-slate-600 dark:text-slate-300')}>Choose for me</span>
              <span className="px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wider shrink-0 bg-violet-100 dark:bg-violet-500/20 text-violet-600 dark:text-violet-400">✨ Recommended</span>
            </div>
            <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">AI analyzes your project and picks the best stack automatically</p>
          </div>
          {stack === 'auto' && (
            <div className="absolute top-2.5 right-2.5">
              <CheckCircle2 className="w-5 h-5 text-violet-500" />
            </div>
          )}
        </button>

        {/* Or pick manually */}
        <div className="flex items-center gap-3 mb-3">
          <div className="flex-1 h-px bg-slate-200 dark:bg-slate-700" />
          <span className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">Or choose manually</span>
          <div className="flex-1 h-px bg-slate-200 dark:bg-slate-700" />
        </div>

        <div className="grid grid-cols-2 gap-3">
          {STACKS.map((s) => (
            <button
              key={s.id}
              onClick={() => onUpdate({ stack: s.id })}
              className={cn(
                'group relative flex items-center gap-4 px-5 py-4 rounded-2xl text-left transition-all duration-200',
                stack === s.id
                  ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                  : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]',
              )}
            >
              <div className={cn('w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-colors',
                stack === s.id ? 'bg-white dark:bg-blue-500/20' : 'bg-slate-50 dark:bg-slate-700/80')}>
                {StackLogos[s.id] ? StackLogos[s.id]() : <Globe className="w-5 h-5 text-slate-400" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={cn('text-[14px] font-semibold truncate', stack === s.id ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>{s.name}</span>
                  {s.tag && (
                    <span className={cn('px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wider shrink-0',
                      s.tag === 'Popular' ? 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
                        : s.tag === 'Backend' ? 'bg-slate-100 dark:bg-slate-600/40 text-slate-500 dark:text-slate-400'
                        : 'bg-slate-200 dark:bg-slate-600 text-slate-500',
                    )}>{s.tag}</span>
                  )}
                </div>
                <p className="text-[12px] text-slate-400 dark:text-slate-500 truncate mt-0.5">{s.description}</p>
              </div>
              {stack === s.id && (
                <div className="absolute top-2.5 right-2.5">
                  <CheckCircle2 className="w-5 h-5 text-blue-500 dark:text-blue-400" />
                </div>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
