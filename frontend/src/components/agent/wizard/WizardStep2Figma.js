'use client';

// ─────────────────────────────────────────────────────────
//  WizardStep2Figma — Figma design import or generate
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';
import { Sparkles, Link2, Check } from 'lucide-react';

export default function WizardStep2Figma({ figmaUrl, skipFigma, onUpdate }) {
  return (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[552px]">
        <div className="text-center mb-6">
          <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Figma design</h2>
          <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Import your design or let AI generate the UI for you.</p>
        </div>

        <div className="space-y-3">
          {/* Option 1: Generate from scratch */}
          <button
            onClick={() => onUpdate({ figmaUrl: '', skipFigma: true })}
            className={cn(
              'w-full flex items-center gap-4 px-5 py-4 rounded-2xl text-left transition-all duration-200',
              skipFigma
                ? 'bg-violet-50/80 dark:bg-violet-500/10 border-2 border-violet-400 dark:border-violet-500 shadow-[0_0_0_3px_rgba(139,92,246,0.08)]'
                : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]',
            )}
          >
            <div className={cn('w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors',
              skipFigma ? 'border-violet-500 bg-violet-500' : 'border-slate-300 dark:border-slate-600')}>
              {skipFigma && <div className="w-2 h-2 rounded-full bg-white" />}
            </div>
            <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
              skipFigma ? 'bg-gradient-to-br from-violet-100 to-blue-100 dark:from-violet-500/20 dark:to-blue-500/20' : 'bg-slate-50 dark:bg-slate-700/80')}>
              <Sparkles className={cn('w-5 h-5', skipFigma ? 'text-violet-600 dark:text-violet-400' : 'text-slate-400')} />
            </div>
            <div className="flex-1 min-w-0">
              <span className={cn('text-[14px] font-semibold', skipFigma ? 'text-violet-700 dark:text-violet-400' : 'text-slate-800 dark:text-slate-200')}>Generate from scratch</span>
              <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">AI will create the UI based on your description</p>
            </div>
            {skipFigma && <Check className="w-4 h-4 text-violet-600 dark:text-violet-400 shrink-0" />}
          </button>

          {/* Option 2: Paste Figma URL */}
          <div
            onClick={() => onUpdate({ skipFigma: false })}
            className={cn(
              'rounded-2xl transition-all duration-200 cursor-pointer',
              !skipFigma
                ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600',
            )}
          >
            <div className="flex items-center gap-4 px-5 pt-4 pb-3">
              <div className={cn('w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors',
                !skipFigma ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600')}>
                {!skipFigma && <div className="w-2 h-2 rounded-full bg-white" />}
              </div>
              <div className="w-10 h-10 rounded-xl bg-white dark:bg-slate-700/80 border border-slate-100 dark:border-slate-600 flex items-center justify-center shrink-0">
                <img src="/icons/figma.svg" alt="Figma" className="w-6 h-6" />
              </div>
              <div className="flex-1 min-w-0">
                <span className={cn('text-[14px] font-semibold', !skipFigma ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>Paste Figma URL</span>
                <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">We'll extract your design tokens and layout</p>
              </div>
            </div>
            {!skipFigma && (
              <div className="px-5 pb-4 pt-1" onClick={(e) => e.stopPropagation()}>
                <div className="relative">
                  <Link2 className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <input
                    type="url"
                    value={figmaUrl}
                    onChange={(e) => onUpdate({ figmaUrl: e.target.value, skipFigma: false })}
                    placeholder="https://www.figma.com/design/..."
                    className="w-full pl-10 pr-4 py-2.5 text-[13px] border border-slate-200 dark:border-slate-600 rounded-xl bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none focus:border-blue-400 dark:focus:border-blue-500 focus:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
