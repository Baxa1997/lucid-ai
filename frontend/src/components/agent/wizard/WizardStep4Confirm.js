'use client';

// ─────────────────────────────────────────────────────────
//  WizardStep4Confirm — Review configuration before build
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';
import { Zap, FileText, Link2, Server, Globe } from 'lucide-react';
import { STACKS } from './StackLogos';

export default function WizardStep4Confirm({ wizardState, autoRecommendation, onGoToStep }) {
  const { stack, description, descriptionFile, figmaUrl, skipFigma, backend, mcpUrl } = wizardState;

  const resolvedStack = stack === 'auto' && autoRecommendation ? autoRecommendation.stack : stack;
  const stackLabel = STACKS.find((s) => s.id === resolvedStack)?.name || (stack === 'auto' ? 'Auto-selected' : '—');
  const backendLabel = { none: 'No backend', supabase: 'Supabase', own: 'Own backend' }[backend] || '—';
  const descLabel = descriptionFile
    ? descriptionFile.name
    : description
    ? (description.length > 80 ? description.slice(0, 80) + '…' : description)
    : '—';
  const figmaLabel = skipFigma ? 'Generate from scratch' : figmaUrl || '—';

  const rows = [
    { label: 'Stack',       value: stackLabel,   icon: <Zap className="w-4 h-4" />,      stepIndex: 0 },
    { label: 'Description', value: descLabel,     icon: <FileText className="w-4 h-4" />,  stepIndex: 1 },
    { label: 'Figma',       value: figmaLabel,    icon: <Link2 className="w-4 h-4" />,     stepIndex: 2, truncate: true },
    { label: 'Backend',     value: backendLabel,  icon: <Server className="w-4 h-4" />,    stepIndex: 3 },
  ];
  if (backend === 'own' && mcpUrl) {
    rows.push({ label: 'MCP URL', value: mcpUrl, icon: <Globe className="w-4 h-4" />, stepIndex: 3, truncate: true });
  }

  return (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[560px]">
        <div className="text-center mb-6">
          <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Confirm your setup</h2>
          <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Review your configuration before we start building.</p>
        </div>
        <div className="bg-white dark:bg-slate-800/60 rounded-2xl border border-slate-200 dark:border-slate-700/80 shadow-sm overflow-hidden">
          {rows.map((r, i) => (
            <div key={i} className={cn('flex items-center gap-3.5 px-5 py-3.5', i > 0 && 'border-t border-slate-100 dark:border-slate-700/50')}>
              <div className="w-8 h-8 rounded-lg bg-slate-50 dark:bg-slate-700/80 flex items-center justify-center shrink-0 text-slate-400 dark:text-slate-500">
                {r.icon}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-0.5">{r.label}</p>
                <p className={cn('text-[13px] font-medium text-slate-700 dark:text-slate-300', r.truncate && 'truncate')}>{r.value}</p>
              </div>
              <button
                onClick={() => onGoToStep(r.stepIndex)}
                className="text-[12px] text-slate-400 dark:text-slate-500 hover:text-blue-600 dark:hover:text-blue-400 font-medium shrink-0 px-2 py-1 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
              >
                Edit
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
