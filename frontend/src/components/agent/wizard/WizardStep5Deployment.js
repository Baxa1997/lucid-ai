'use client';

// ─────────────────────────────────────────────────────────
//  WizardStep5Deployment — Choose deployment target
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';
import { Rocket, Github } from 'lucide-react';

const DEPLOYMENT_OPTS = [
  {
    id: 'hosted',
    icon: Rocket,
    label: 'Host it for me',
    sub: 'Free subdomain — instant deploy',
    bg: 'bg-blue-50 dark:bg-blue-500/10',
    color: 'text-blue-600 dark:text-blue-400',
    badge: 'Free',
    bc: 'bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-400',
  },
  {
    id: 'own',
    icon: Github,
    label: 'Deploy to my account',
    sub: 'Export to GitHub after build',
    bg: 'bg-slate-50 dark:bg-slate-700/80',
    color: 'text-slate-600 dark:text-slate-400',
    badge: 'Later',
    bc: 'bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400',
  },
];

export default function WizardStep5Deployment({ deployment, onUpdate }) {
  return (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[552px]">
        <div className="text-center mb-8">
          <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Deployment</h2>
          <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Where should we deploy your project?</p>
        </div>
        <div className="space-y-3">
          {DEPLOYMENT_OPTS.map((o) => {
            const Icon = o.icon;
            return (
              <button
                key={o.id}
                onClick={() => onUpdate({ deployment: o.id })}
                className={cn(
                  'w-full flex items-center gap-4 px-5 py-4 rounded-2xl transition-all duration-200 text-left',
                  deployment === o.id
                    ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                    : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]',
                )}
              >
                <div className={cn('w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors',
                  deployment === o.id ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600')}>
                  {deployment === o.id && <div className="w-2 h-2 rounded-full bg-white" />}
                </div>
                <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center shrink-0', o.bg)}>
                  <span className={o.color}><Icon className="w-5 h-5" /></span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={cn('text-[14px] font-semibold', deployment === o.id ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>{o.label}</span>
                    <span className={cn('px-1.5 py-0.5 text-[10px] font-bold rounded', o.bc)}>{o.badge}</span>
                  </div>
                  <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">{o.sub}</p>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
